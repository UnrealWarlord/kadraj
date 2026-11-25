#!/usr/bin/env bash
# KADRAJ kurulum betiği — yönetici (sudo) gerektirmez, yalnızca ev dizinine yazar.
#   ~/.local/share/kadraj                      uygulama dosyaları
#   ~/.local/bin/kadraj                        komut (uygulamaya sembolik bağ)
#   ~/.local/share/applications/kadraj.desktop uygulama menüsü girdisi
# Kullanıcı verisi (~/.kadraj) hiçbir zaman buraya dokunulmaz.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${HOME:-}" ] || [ "$HOME" = "/" ]; then
    echo "HATA: HOME ortam değişkeni geçerli değil; kurulum durduruldu." >&2
    exit 1
fi
APP_DIR="$HOME/.local/share/kadraj"
BIN_DIR="$HOME/.local/bin"
LINK="$BIN_DIR/kadraj"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP="$APPS_DIR/kadraj.desktop"

if [ ! -x "$SRC/KADRAJ" ] || [ ! -d "$SRC/_internal" ]; then
    echo "HATA: KADRAJ ve _internal bu betiğin yanında bulunamadı." >&2
    echo "      Arşivi tamamen çıkardığınızdan ve betiği çıkan klasörün içinden çalıştırdığınızdan emin olun." >&2
    exit 1
fi

echo "KADRAJ kuruluyor: $APP_DIR"
mkdir -p "$BIN_DIR" "$APPS_DIR" "$(dirname "$APP_DIR")"

# Önce yan klasöre kopyala, sonra yerine taşı: yarım kalan kurulum eskisini bozmasın (güncelleme de bu yoldan).
STAGE="$(mktemp -d "$APP_DIR.yeni.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
chmod 755 "$STAGE"
cp -a "$SRC/KADRAJ" "$SRC/_internal" "$STAGE/"
cp -a "$SRC/uninstall.sh" "$STAGE/"
[ -f "$SRC/KURULUM.txt" ] && cp -a "$SRC/KURULUM.txt" "$STAGE/"
rm -rf "$APP_DIR"
mv "$STAGE" "$APP_DIR"
trap - EXIT

ln -sfn "$APP_DIR/KADRAJ" "$LINK"

# .desktop: Exec yolundaki % işaretleri iki katına çıkar, boşluklara karşı tırnak içine alınır.
EXEC_PATH="${APP_DIR//\%/%%}/KADRAJ"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=KADRAJ
Comment=Video, ses, fotoğraf ve dizi stüdyosu
Exec="$EXEC_PATH"
Icon=applications-multimedia
Terminal=false
Categories=AudioVideo;Video;
EOF
chmod 644 "$DESKTOP"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

echo "Kuruldu."
echo "  Başlatmak için: uygulama menüsünde \"KADRAJ\" ya da terminalde  kadraj"

# --- uyarılar (kurulum bunlar olmadan da tamamlanır) ---------------------------------------------
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo
       echo "UYARI: $BIN_DIR, PATH içinde değil; terminalde 'kadraj' yazmak için oturumu kapatıp açın"
       echo "       ya da şu satırı ~/.profile dosyasına ekleyin:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# Dağıtıma uygun kurulum komutunu seç.
DISTRO=""
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    DISTRO="$(. /etc/os-release; echo " ${ID:-} ${ID_LIKE:-} ")"
fi
case "$DISTRO" in
    *" arch "*|*" manjaro "*|*" cachyos "*) HINT="sudo pacman -S ffmpeg portaudio" ;;
    *" fedora "*|*" rhel "*)                HINT="sudo dnf install portaudio ffmpeg-free   (tam FFmpeg için RPM Fusion: sudo dnf install ffmpeg)" ;;
    *" debian "*|*" ubuntu "*)              HINT="sudo apt install ffmpeg libportaudio2" ;;
    *)                                      HINT="dağıtımınızın paket yöneticisiyle 'ffmpeg' ve 'portaudio' (libportaudio2) paketlerini kurun" ;;
esac

have_lib() {   # have_lib libportaudio.so.2
    local ldc="" c d
    for c in "$(command -v ldconfig 2>/dev/null || true)" /sbin/ldconfig /usr/sbin/ldconfig; do
        if [ -n "$c" ] && [ -x "$c" ]; then ldc="$c"; break; fi
    done
    if [ -n "$ldc" ] && "$ldc" -p 2>/dev/null | grep -q "$1"; then return 0; fi
    for d in /usr/lib /usr/lib64 /lib /lib64 /usr/lib/x86_64-linux-gnu /usr/local/lib; do
        compgen -G "$d/$1*" >/dev/null && return 0
    done
    return 1
}

MISSING=""
command -v ffmpeg  >/dev/null 2>&1 || MISSING="$MISSING ffmpeg"
command -v ffprobe >/dev/null 2>&1 || MISSING="$MISSING ffprobe"
have_lib libportaudio.so.2 || MISSING="$MISSING libportaudio"
if [ -n "$MISSING" ]; then
    echo
    echo "UYARI: şu sistem bileşenleri bulunamadı:$MISSING"
    echo "       Video düzenleme (ffmpeg) ve ses kaydı (portaudio) bunlar olmadan çalışmaz."
    echo "       Kurmak için:  $HINT"
fi
exit 0
