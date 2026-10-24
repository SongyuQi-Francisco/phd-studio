#!/usr/bin/env bash
# Build "PhD Studio.app" (macOS): double-click starts the local server if needed
# and opens the workbench in its own window. Usage: launcher/make_app.sh [target dir, default ~/Desktop]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STUDIO="$(cd "$HERE/.." && pwd)"
TARGET="${1:-$HOME/Desktop}"
APP="$TARGET/PhD Studio.app"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---- icon: SVG -> 1024px PNG (headless Chrome keeps transparency) -> .icns
if [ -x "$CHROME" ]; then
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --default-background-color=00000000 \
    --window-size=1024,1024 --screenshot="$WORK/icon.png" "file://$HERE/icon.svg" >/dev/null 2>&1
else
  qlmanage -t -s 1024 -o "$WORK" "$HERE/icon.svg" >/dev/null 2>&1 && mv "$WORK/icon.svg.png" "$WORK/icon.png"
fi
mkdir -p "$WORK/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$WORK/icon.png" --out "$WORK/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$WORK/icon.png" --out "$WORK/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$WORK/AppIcon.iconset" -o "$WORK/AppIcon.icns"

# ---- bundle: a standard AppleScript applet (a real executable) that runs Resources/launch.sh
rm -rf "$APP"
osacompile -o "$APP" -e 'do shell script quoted form of (POSIX path of (path to resource "launch.sh"))'
PLIST="$APP/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "$1" "$PLIST" >/dev/null 2>&1 || true; }
pb "Delete :CFBundleIconName"
pb "Set :CFBundleIconFile applet"
pb "Add :CFBundleIdentifier string local.phd-studio.launcher"
pb "Set :CFBundleIdentifier local.phd-studio.launcher"
pb "Set :CFBundleName PhD Studio"
pb "Add :LSUIElement bool true"
rm -f "$APP/Contents/Resources/Assets.car"
cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/applet.icns"

sed "s|@STUDIO@|$STUDIO|" > "$APP/Contents/Resources/launch.sh" <<'SCRIPT'
#!/bin/bash
# Start the PhD Studio server if it is not running, then open the workbench window.
STUDIO="@STUDIO@"
URL="http://127.0.0.1:8770"
LOG="$HOME/Library/Logs/PhD Studio.log"
up() { curl -s -m 1 -o /dev/null "$URL/api/status"; }

if ! up; then
  nohup "$STUDIO/start.sh" >>"$LOG" 2>&1 </dev/null &
  for _ in $(seq 1 80); do sleep 0.25; up && break; done
  if ! up; then
    osascript -e 'display dialog "PhD Studio 没能启动。详细原因在 ~/Library/Logs/PhD Studio.log" buttons {"好"} default button 1 with icon caution with title "PhD Studio"' >/dev/null
    exit 1
  fi
fi

if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app="$URL" --window-size=1440,900
else
  open "$URL"
fi
SCRIPT
chmod +x "$APP/Contents/Resources/launch.sh"
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
touch "$APP"
echo "Built: $APP"
