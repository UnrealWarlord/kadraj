#!/usr/bin/env bash
# KADRAJ Linux paketini üretir:
#   dist/KADRAJ-<sürüm>-linux-x86_64.tar.gz
#   dist/KADRAJ-<sürüm>-linux-x86_64.tar.gz.sha256
# <sürüm>, kadraj/version.py içinden okunur. Gereken tek şey: Docker.
#
# Derleme AlmaLinux 9 (glibc 2.34) imajında yapılır → paket Ubuntu 22.04+, Debian 12+, Fedora 35+ üzerinde çalışır.
# İmaj içinde, paketlenmiş KADRAJ'ın `--selftest` çıktısı "SELFTEST TAMAM" vermezse derleme başarısız olur.
#
# Kullanım:   bash packaging/build_linux.sh [--rmi]
#   --rmi   bittikten sonra derleme imajını sil (varsayılan: sonraki derlemeler hızlı olsun diye kalır)
set -euo pipefail

die() { echo "HATA: $*" >&2; exit 1; }

REMOVE_IMAGE=0
for arg in "$@"; do
    case "$arg" in
        --rmi) REMOVE_IMAGE=1 ;;
        *) die "bilinmeyen seçenek: $arg (kullanım: bash packaging/build_linux.sh [--rmi])" ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v docker >/dev/null 2>&1 || die "Docker bulunamadı; kurup tekrar deneyin."

VERSION="$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' kadraj/version.py | head -n1)"
[ -n "$VERSION" ] || die "kadraj/version.py içinde __version__ bulunamadı."
NAME="KADRAJ-${VERSION}-linux-x86_64"
IMAGE="kadraj-linux-build:${VERSION}"
WORK="$ROOT/build/linux"
OUT="$ROOT/dist"

echo "==> KADRAJ ${VERSION} Linux paketi derleniyor (${NAME})"
rm -rf "$WORK"
mkdir -p "$WORK/ctx" "$WORK/pyi" "$OUT"

# Docker bağlamı yalnızca requirements.txt içerir (kaynak imaja girmez, bağlam küçük kalır).
cp requirements.txt "$WORK/ctx/requirements.txt"
echo "==> Derleme imajı hazırlanıyor ($IMAGE)"
docker build -f packaging/Dockerfile.linux -t "$IMAGE" "$WORK/ctx"

# Depo salt-okunur bağlanır; çıktı /out (= build/linux/pyi) altına, kullanıcının kendi uid'iyle yazılır.
echo "==> PyInstaller + selftest (Docker içinde)"
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e PYINSTALLER_CONFIG_DIR=/tmp/pyinstaller-cfg \
    -e PYTHONDONTWRITEBYTECODE=1 -e QT_QPA_PLATFORM=offscreen \
    -v "$ROOT":/src:ro -v "$WORK/pyi":/out \
    "$IMAGE" bash -euo pipefail -c '
        cd /src
        python3.12 -m PyInstaller packaging/kadraj.spec --noconfirm --clean \
            --distpath /out/dist --workpath /out/work
        echo "==> Paketlenmiş KADRAJ --selftest"
        cd /out/dist/KADRAJ
        set +e
        timeout 300 ./KADRAJ --selftest > /out/selftest.log 2>&1
        rc=$?
        set -e
        tail -n 25 /out/selftest.log
        if [ "$rc" -ne 0 ] || ! grep -q "SELFTEST TAMAM" /out/selftest.log; then
            echo "HATA: paketlenmiş KADRAJ selftest geçemedi (çıkış kodu $rc)" >&2
            exit 1
        fi
    '

echo "==> Arşiv hazırlanıyor"
STAGE="$WORK/stage/$NAME"
mkdir -p "$STAGE"
cp -a "$WORK/pyi/dist/KADRAJ/." "$STAGE/"
cp packaging/linux/install.sh packaging/linux/uninstall.sh packaging/linux/KURULUM.txt "$STAGE/"
chmod 755 "$STAGE/install.sh" "$STAGE/uninstall.sh" "$STAGE/KADRAJ"
chmod 644 "$STAGE/KURULUM.txt"

TAR_EXTRA=()
[ -n "${SOURCE_DATE_EPOCH:-}" ] && TAR_EXTRA+=("--mtime=@${SOURCE_DATE_EPOCH}")
rm -f "$OUT/$NAME.tar.gz" "$OUT/$NAME.tar.gz.sha256"
tar --sort=name --owner=0 --group=0 --numeric-owner ${TAR_EXTRA[@]+"${TAR_EXTRA[@]}"} -C "$WORK/stage" -cf - "$NAME" \
    | gzip -9n > "$OUT/$NAME.tar.gz"
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" && sha256sum -c "$NAME.tar.gz.sha256" )

rm -rf "$WORK"
if [ "$REMOVE_IMAGE" -eq 1 ]; then
    docker rmi "$IMAGE" >/dev/null && echo "==> Derleme imajı silindi ($IMAGE)"
else
    echo "==> Derleme imajı ($IMAGE) sonraki derlemeler için duruyor; silmek için: docker rmi $IMAGE"
fi

echo
echo "Hazır:"
ls -l "$OUT/$NAME.tar.gz" "$OUT/$NAME.tar.gz.sha256"
cat "$OUT/$NAME.tar.gz.sha256"
