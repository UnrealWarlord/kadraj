#!/usr/bin/env bash
# KADRAJ kaldırma betiği — install.sh'ın yazdığı üç şeyi siler; kullanıcı verisine (~/.kadraj) dokunmaz.
set -euo pipefail

if [ -z "${HOME:-}" ] || [ "$HOME" = "/" ]; then
    echo "HATA: HOME ortam değişkeni geçerli değil; kaldırma durduruldu." >&2
    exit 1
fi
APP_DIR="$HOME/.local/share/kadraj"
LINK="$HOME/.local/bin/kadraj"
DESKTOP="$HOME/.local/share/applications/kadraj.desktop"

# Sembolik bağı yalnızca bizim kurulumumuza işaret ediyorsa sil (başka bir 'kadraj' komutunu bozma).
if [ -L "$LINK" ]; then
    case "$(readlink "$LINK")" in
        "$APP_DIR"/*) rm -f "$LINK" ;;
        *) echo "Atlandı: $LINK başka bir yere işaret ediyor." ;;
    esac
fi
rm -f "$DESKTOP"
rm -rf "$APP_DIR"
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

echo "KADRAJ kaldırıldı."
echo "Projeleriniz ve ayarlarınız silinmedi (~/.kadraj ve proje klasörleriniz yerinde)."
echo "Onlar da gitsin istiyorsanız:  rm -rf ~/.kadraj"
