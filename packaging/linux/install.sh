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
[ -f "$SRC/THIRD_PARTY_NOTICES.md" ] && cp -a "$SRC/THIRD_PARTY_NOTICES.md" "$STAGE/"
rm -rf "$APP_DIR"
mv "$STAGE" "$APP_DIR"
trap - EXIT

ln -sfn "$APP_DIR/KADRAJ" "$LINK"

# Simgeler: ~/.local/share/icons/hicolor/<n>x<n>/apps/kadraj.png (paketin içindeki kadraj-<n>.png dosyalarından).
ICON_SRC="$APP_DIR/_internal/kadraj/ui/assets/icon"
ICON_ROOT="$HOME/.local/share/icons/hicolor"
for n in 16 24 32 48 64 128 256 512; do
    if [ -f "$ICON_SRC/kadraj-$n.png" ]; then
        mkdir -p "$ICON_ROOT/${n}x${n}/apps"
        cp -f "$ICON_SRC/kadraj-$n.png" "$ICON_ROOT/${n}x${n}/apps/kadraj.png"
    fi
done
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$ICON_ROOT" >/dev/null 2>&1 || true

# .desktop: Exec yolundaki % işaretleri iki katına çıkar, boşluklara karşı tırnak içine alınır.
# StartupWMClass=kadraj: uygulama setDesktopFileName("kadraj") der; görev çubuğu bu girdiyi/simgeyi eşleştirir.
EXEC_PATH="${APP_DIR//\%/%%}/KADRAJ"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=KADRAJ
Comment=Video, ses, fotoğraf ve dizi stüdyosu
Exec="$EXEC_PATH"
Icon=kadraj
StartupWMClass=kadraj
Terminal=false
Categories=AudioVideo;Video;
EOF
chmod 644 "$DESKTOP"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

echo "Kuruldu."
echo "  Başlatmak için: uygulama menüsünde \"KADRAJ\" ya da terminalde  kadraj"

# --- uyarı (kurulum bunsuz da tamamlanır) ----------------------------------------------------------
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo
       echo "UYARI: $BIN_DIR, PATH içinde değil; terminalde 'kadraj' yazmak için oturumu kapatıp açın"
       echo "       ya da şu satırı ~/.profile dosyasına ekleyin:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# FFmpeg, ffprobe, Deno, PortAudio ve libxcb-cursor pakete dahildir; sistemden bir şey kurmak gerekmez.
exit 0
