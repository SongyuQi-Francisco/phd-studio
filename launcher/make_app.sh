#!/usr/bin/env bash
# Build "PhD Studio.app" (macOS): a real app whose native entry point (launcher/stub.c)
# runs the workbench window in-process, so the Dock, ⌘Tab and the menu bar show
# "PhD Studio" with its icon. Falls back to an AppleScript applet if no C compiler.
# Usage: launcher/make_app.sh [target dir, default ~/Desktop]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STUDIO="$(cd "$HERE/.." && pwd)"
TARGET="${1:-$HOME/Desktop}"
APP="$TARGET/PhD Studio.app"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
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
cp "$WORK/icon.png" "$HERE/icon.png"  # Dock icon for a window started outside the app (git-ignored)

rm -rf "$APP"
CLANG="$(xcrun --find clang 2>/dev/null || command -v clang || true)"

if [ -n "$CLANG" ]; then
  # ---- native bundle
  (cd "$STUDIO" && "$UV" sync --quiet)
  PY="$STUDIO/.venv/bin/python"
  PYHOME="$("$PY" -c 'import sys; print(sys.base_prefix)')"
  PYLIB="$("$PY" -c 'import os, sysconfig; print(os.path.join(sysconfig.get_config_var("LIBDIR"), sysconfig.get_config_var("LDLIBRARY")))')"
  SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  SDK="$(xcrun --show-sdk-path 2>/dev/null || true)"
  "$CLANG" -O2 ${SDK:+-isysroot "$SDK"} -o "$APP/Contents/MacOS/PhDStudio" "$HERE/stub.c" \
    -DPYHOME="\"$PYHOME\"" -DPYLIB="\"$PYLIB\"" -DSITE="\"$SITE\"" -DSTUDIO="\"$STUDIO\""
  cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>PhD Studio</string>
  <key>CFBundleDisplayName</key><string>PhD Studio</string>
  <key>CFBundleIdentifier</key><string>local.phd-studio</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>PhDStudio</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
else
  # ---- fallback: AppleScript applet that starts the window via uv
  osacompile -o "$APP" -e 'do shell script quoted form of (POSIX path of (path to resource "launch.sh"))'
  PLIST="$APP/Contents/Info.plist"
  pb() { /usr/libexec/PlistBuddy -c "$1" "$PLIST" >/dev/null 2>&1 || true; }
  pb "Delete :CFBundleIconName"
  pb "Set :CFBundleIconFile applet"
  pb "Set :CFBundleIdentifier local.phd-studio.launcher"
  pb "Set :CFBundleName PhD Studio"
  pb "Add :LSUIElement bool true"
  rm -f "$APP/Contents/Resources/Assets.car"
  cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/applet.icns"
  sed "s|@STUDIO@|$STUDIO|" > "$APP/Contents/Resources/launch.sh" <<'SCRIPT'
#!/bin/bash
STUDIO="@STUDIO@"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
cd "$STUDIO" || exit 1
nohup "$UV" run --quiet python -m app.desktop >>"$HOME/Library/Logs/PhD Studio.log" 2>&1 </dev/null &
SCRIPT
  chmod +x "$APP/Contents/Resources/launch.sh"
fi

codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
touch "$APP"
echo "Built: $APP"
