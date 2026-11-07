#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/bds-omni-manager"
APPS="$HOME/.local/share/applications"
mkdir -p "$DEST" "$APPS"
cp -a "$HERE/main.py" "$HERE/run.sh" "$HERE/static" "$HERE/assets" "$DEST/"
chmod +x "$DEST/main.py" "$DEST/run.sh"
cat > "$APPS/bds-omni-manager.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=BDS Omni Manager
Comment=AMOLED Bedrock Dedicated Server control panel
Exec="$DEST/run.sh"
Icon=$DEST/assets/icon.svg
Terminal=true
Categories=Game;Utility;
StartupNotify=true
EOF
update-desktop-database "$APPS" >/dev/null 2>&1 || true
printf '\nInstalled BDS Omni Manager. Launch it from your app menu or run:\n  %s/run.sh\n\n' "$DEST"
