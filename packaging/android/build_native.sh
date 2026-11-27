#!/usr/bin/env bash
# KADRAJ · Android yerel araçları (docs/steps/MOBILE_3.md, karar M13/M14):
#   FFmpeg (ffmpeg + ffprobe; x264, dav1d, libass, FreeType, FriBidi, HarfBuzz ile) ve QuickJS-ng — arm64-v8a, API 28.
#
# Android 10+ yazılabilir klasörden ikili çalıştırmaz (W^X). Araçlar APK'nın yerel kütüphane klasörüne lib<ad>.so
# adıyla konur (buildozer: android.add_libs_arm64_v8a) ve oradan çalıştırılır (kadraj/core/components.py).
# Hepsi STATİK bağlıdır: yanlarında başka .so gerekmez, QtMultimedia'nın kendi FFmpeg kütüphaneleriyle (libavcodec.so…)
# çakışmaz ve aynı dosyalar Linux'ta qemu-aarch64 ile sınanabilir (packaging/android/test_native.sh).
#
# Kullanım (packaging/android/Dockerfile imajında, root olmayan kullanıcıyla):
#   ANDROID_NDK_HOME=/yol/android-ndk-r28c packaging/android/build_native.sh [ÇIKTI_KLASÖRÜ]
# Ortam: KADRAJ_NATIVE_WORK (kaynak/derleme önbelleği, varsayılan packaging/android/build/native-work), JOBS.
# Çıktı: <ÇIKTI>/arm64-v8a/{libffmpeg.so,libffprobe.so,libqjs.so}, <ÇIKTI>/licenses/, <ÇIKTI>/VERSIONS.txt,
#        <ÇIKTI>/android-native-sources.tar (kaynakların tam arşivi + bu betik: GPL karşılığı kaynak),
#        <ÇIKTI>/SHA256SUMS. Varsayılan ÇIKTI: packaging/android/build/native (git'e girmez: build/).
# Kaynaklar GitHub'dan sabit commit ile alınır (ffmpeg.org / code.videolan.org yerine resmî yansılar; x264 için
# yaygın kullanılan mirror/x264 "stable" dalı). Commit değişirse betik durur.
set -euo pipefail

API=28
ABI=arm64-v8a
TRIPLE=aarch64-linux-android
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "${1:-$HERE/build/native}" && cd "${1:-$HERE/build/native}" && pwd)
WORK=${KADRAJ_NATIVE_WORK:-$HERE/build/native-work}
JOBS=${JOBS:-$(nproc)}

# ad | depo | sürüm (yalnız bilgi) | commit
SOURCES=(
  "ffmpeg|https://github.com/FFmpeg/FFmpeg|n9.1|74e4393595cf2b5857f0da925c4e0dd74ea306e5"
  "x264|https://github.com/mirror/x264|stable|31e19f92f00c7003fa115047ce50978bc98c3a0d"
  "dav1d|https://github.com/videolan/dav1d|1.5.5|6c7bee685677fd997a431302be4fea8ea4017606"
  "freetype|https://github.com/freetype/freetype|VER-2-15-0|37515cf56ba3349656049edbed89ffc6f6466f2d"
  "fribidi|https://github.com/fribidi/fribidi|v1.0.18|15393e4937aa95bcf865d92143735746aeb654fa"
  "harfbuzz|https://github.com/harfbuzz/harfbuzz|15.2.0|ed51d202cfd0c29372d3ee056eb0dc715688a410"
  "libass|https://github.com/libass/libass|0.17.5|4a05d8127f525943ebf45fdc6497c9e665947f0d"
  "quickjs|https://github.com/quickjs-ng/quickjs|v0.18.0|d7215b0ad3fc2c562e8317fd9bca058fd4863804"
)

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'HATA: %s\n' "$*" >&2; exit 1; }

# --- NDK ---------------------------------------------------------------------------------------------------------
NDK=${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-${ANDROID_NDK:-}}}
if [ -z "$NDK" ]; then
  NDK=$(ls -d "$HOME"/.pyside6_android_deploy/android-ndk/android-ndk-r*/ 2>/dev/null | sort -V | tail -1 || true)
  NDK=${NDK%/}
fi
[ -n "$NDK" ] && [ -d "$NDK" ] || die "Android NDK bulunamadı (ANDROID_NDK_HOME ver)."
TC="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
SYSROOT="$TC/sysroot"
export CC="$TC/bin/${TRIPLE}${API}-clang" CXX="$TC/bin/${TRIPLE}${API}-clang++"
export AR="$TC/bin/llvm-ar" RANLIB="$TC/bin/llvm-ranlib" STRIP="$TC/bin/llvm-strip" NM="$TC/bin/llvm-nm"
[ -x "$CC" ] || die "Derleyici yok: $CC"
command -v meson >/dev/null || die "meson yok (pip install meson)."

SRC="$WORK/src"; BUILD="$WORK/build"; PREFIX="$WORK/prefix"
mkdir -p "$SRC" "$BUILD" "$PREFIX"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig" PKG_CONFIG_PATH=""     # yalnız bizim statik kütüphaneler
CFLAGS_COMMON="-O2 -fPIC -ffunction-sections -fdata-sections"
LDFLAGS_COMMON="-Wl,--gc-sections -Wl,-z,max-page-size=16384"          # 16 KB sayfalı cihazlar (Android 15+)

# --- kaynaklar ---------------------------------------------------------------------------------------------------
fetch() {
  local name=$1 url=$2 commit=$3 dir="$SRC/$1"
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ]; then
    git -C "$dir" reset -q --hard && git -C "$dir" clean -qfdx
    return
  fi
  rm -rf "$dir"
  git init -q "$dir"
  git -C "$dir" fetch -q --depth 1 "$url" "$commit"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ] || die "$name: commit eşleşmedi"
}
for entry in "${SOURCES[@]}"; do
  IFS='|' read -r name url version commit <<<"$entry"
  log "kaynak: $name"
  fetch "$name" "$url" "$commit"
done
# Etiketsiz (commit'le) alınan kaynakta FFmpeg sürümü commit kısaltması olurdu; sürüm dosyası "ffmpeg version 9.1" yazdırır
# (FFmpeg 9: FF_VERSION, daha eskiler: VERSION).
FF_RELEASE=$(printf '%s\n' "${SOURCES[@]}" | grep '^ffmpeg|' | cut -d'|' -f3 | sed 's/^n//')
printf '%s\n' "$FF_RELEASE" | tee "$SRC/ffmpeg/FF_VERSION" >"$SRC/ffmpeg/VERSION"

# --- meson çapraz derleme dosyası --------------------------------------------------------------------------------
CROSS="$BUILD/android-$ABI.cross"
cat >"$CROSS" <<EOF
[binaries]
c = '$CC'
cpp = '$CXX'
ar = '$AR'
strip = '$STRIP'
ranlib = '$RANLIB'
pkg-config = 'pkg-config'

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8-a'
endian = 'little'

[built-in options]
c_args = [$(printf "'%s'," $CFLAGS_COMMON)]
cpp_args = [$(printf "'%s'," $CFLAGS_COMMON)]
c_link_args = [$(printf "'%s'," $LDFLAGS_COMMON)]
cpp_link_args = [$(printf "'%s'," $LDFLAGS_COMMON) '-static-libstdc++']
default_library = 'static'
buildtype = 'release'
wrap_mode = 'nodownload'

[properties]
pkg_config_libdir = '$PREFIX/lib/pkgconfig'
EOF

meson_build() {   # meson_build <kaynak-adı> [seçenekler…]
  local name=$1; shift
  local bdir="$BUILD/$name"
  rm -rf "$bdir"
  meson setup "$bdir" "$SRC/$name" --cross-file "$CROSS" --prefix "$PREFIX" --libdir lib "$@"
  ninja -C "$bdir" -j "$JOBS"
  ninja -C "$bdir" install >/dev/null
}

# --- bağımlılıklar -----------------------------------------------------------------------------------------------
log "x264"
(
  cd "$SRC/x264"
  ./configure --prefix="$PREFIX" --host="$TRIPLE" --cross-prefix="$TC/bin/llvm-" --sysroot="$SYSROOT" \
    --enable-static --enable-pic --disable-cli --disable-opencl \
    --extra-cflags="$CFLAGS_COMMON" --extra-ldflags="$LDFLAGS_COMMON"
  make -j "$JOBS"
  make install >/dev/null
)

log "dav1d"
meson_build dav1d -Denable_tools=false -Denable_tests=false -Denable_examples=false -Denable_docs=false \
  -Dxxhash_muxer=disabled

log "freetype"
meson_build freetype -Dbrotli=disabled -Dbzip2=disabled -Dharfbuzz=disabled -Dpng=disabled -Dzlib=disabled \
  -Dtests=disabled -Dmmap=enabled -Dhvf=disabled

log "fribidi"
meson_build fribidi -Ddocs=false -Dbin=false -Dtests=false -Ddeprecated=false

log "harfbuzz"
meson_build harfbuzz -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled \
  -Dpng=disabled -Dzlib=disabled -Dicu=disabled -Dgraphite2=disabled -Draster=disabled -Dvector=disabled \
  -Dgpu=disabled -Dgpu_demo=disabled -Dsubset=disabled -Dtests=disabled -Dintrospection=disabled -Ddocs=disabled \
  -Dutilities=disabled -Dbenchmark=disabled

log "libass"
meson_build libass -Dfontconfig=disabled -Ddirectwrite=disabled -Dcoretext=disabled -Drequire-system-font-provider=false \
  -Dlibunibreak=disabled -Dasm=enabled -Dtest=disabled -Dcompare=disabled -Dprofile=disabled -Dfuzz=disabled \
  -Dcheckasm=disabled

# --- FFmpeg ------------------------------------------------------------------------------------------------------
log "FFmpeg"
FFBUILD="$BUILD/ffmpeg"
rm -rf "$FFBUILD" && mkdir -p "$FFBUILD"
FFMPEG_CONFIGURE=(
  --prefix="$BUILD/ffmpeg-install"
  --target-os=android --arch=aarch64 --cpu=armv8-a --enable-cross-compile
  --cc="$CC" --cxx="$CXX" --ar="$AR" --ranlib="$RANLIB" --strip="$STRIP" --nm="$NM"
  --sysroot="$SYSROOT"
  --pkg-config=pkg-config --pkg-config-flags=--static
  --enable-gpl --enable-static --disable-shared --enable-pic
  --disable-autodetect --enable-zlib --enable-iconv
  --enable-libx264 --enable-libdav1d --enable-libass --enable-libfreetype --enable-libfribidi --enable-libharfbuzz
  --disable-doc --disable-ffplay --enable-ffmpeg --enable-ffprobe
  --disable-indevs --enable-indev=lavfi --disable-outdevs
  --disable-debug
  --extra-cflags="$CFLAGS_COMMON -I$PREFIX/include"
  --extra-ldflags="-L$PREFIX/lib $LDFLAGS_COMMON"
  --extra-ldexeflags="-static"
  --extra-libs="-lc++_static -lc++abi -lm"
)
(
  cd "$FFBUILD"
  "$SRC/ffmpeg/configure" "${FFMPEG_CONFIGURE[@]}" || { tail -60 ffbuild/config.log; exit 1; }
  make -j "$JOBS"
)

# --- QuickJS-ng --------------------------------------------------------------------------------------------------
log "QuickJS-ng"
QJSBUILD="$BUILD/quickjs"
rm -rf "$QJSBUILD"
cmake -S "$SRC/quickjs" -B "$QJSBUILD" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI="$ABI" -DANDROID_PLATFORM="android-$API" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DQJS_BUILD_CLI_STATIC=ON -DQJS_BUILD_EXAMPLES=OFF \
  -DCMAKE_C_FLAGS="$CFLAGS_COMMON" -DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS_COMMON"
ninja -C "$QJSBUILD" -j "$JOBS" qjs_exe

# --- çıktı -------------------------------------------------------------------------------------------------------
log "çıktı: $OUT"
rm -rf "$OUT/$ABI" "$OUT/licenses"
mkdir -p "$OUT/$ABI" "$OUT/licenses"
install -m 0755 "$FFBUILD/ffmpeg" "$OUT/$ABI/libffmpeg.so"
install -m 0755 "$FFBUILD/ffprobe" "$OUT/$ABI/libffprobe.so"
install -m 0755 "$QJSBUILD/qjs" "$OUT/$ABI/libqjs.so"
"$STRIP" --strip-unneeded "$OUT/$ABI"/*.so
for f in "$OUT/$ABI"/*.so; do                 # yorumlayıcısız (PT_INTERP yok) sabit adresli çalıştırılabilir olmalı
  "$TC/bin/llvm-readelf" -h "$f" | grep -q "EXEC (Executable file)" || die "$(basename "$f") çalıştırılabilir değil"
  if "$TC/bin/llvm-readelf" -l "$f" | grep -q "INTERP"; then die "$(basename "$f") statik değil"; fi
done

cp "$SRC/ffmpeg/COPYING.GPLv2" "$OUT/licenses/FFmpeg-COPYING.GPLv2"
cp "$SRC/ffmpeg/LICENSE.md" "$OUT/licenses/FFmpeg-LICENSE.md"
cp "$SRC/x264/COPYING" "$OUT/licenses/x264-COPYING"
cp "$SRC/dav1d/COPYING" "$OUT/licenses/dav1d-COPYING"
cp "$SRC/freetype/LICENSE.TXT" "$OUT/licenses/FreeType-LICENSE.TXT"
cp "$SRC/freetype/docs/FTL.TXT" "$OUT/licenses/FreeType-FTL.TXT"
cp "$SRC/fribidi/COPYING" "$OUT/licenses/FriBidi-COPYING"
cp "$SRC/harfbuzz/COPYING" "$OUT/licenses/HarfBuzz-COPYING"
cp "$SRC/libass/COPYING" "$OUT/licenses/libass-COPYING"
cp "$SRC/quickjs/LICENSE" "$OUT/licenses/QuickJS-ng-LICENSE"

{
  echo "KADRAJ Android yerel araçları · $ABI · API $API · NDK $(basename "$NDK")"
  echo "Derleyen betik: packaging/android/build_native.sh"
  echo
  for entry in "${SOURCES[@]}"; do
    IFS='|' read -r name url version commit <<<"$entry"
    printf '%-9s %-12s %s  %s\n' "$name" "$version" "$commit" "$url"
  done
  echo
  echo "FFmpeg configure:"
  printf '  %s\n' "${FFMPEG_CONFIGURE[@]}" | sed "s|$WORK|<work>|g; s|$NDK|<ndk>|g"
} >"$OUT/VERSIONS.txt"

# GPL karşılığı kaynak: kullanılan kaynakların tam arşivi + bu betik; APK ile aynı Release'e eklenir (7. adım, CI)
log "kaynak arşivi"
SRCPKG="$WORK/sources-pkg"
rm -rf "$SRCPKG"
mkdir -p "$SRCPKG"
for entry in "${SOURCES[@]}"; do
  IFS='|' read -r name url version commit <<<"$entry"
  git -C "$SRC/$name" archive --format=tar.gz --prefix="$name-$version/" -o "$SRCPKG/$name-$version.tar.gz" "$commit"
done
cp "$HERE/build_native.sh" "$OUT/VERSIONS.txt" "$SRCPKG/"
tar -C "$SRCPKG" -cf "$OUT/android-native-sources.tar" .
rm -rf "$SRCPKG"

(cd "$OUT" && sha256sum "$ABI"/*.so >SHA256SUMS)
ls -l "$OUT/$ABI" "$OUT/android-native-sources.tar"
cat "$OUT/SHA256SUMS"
