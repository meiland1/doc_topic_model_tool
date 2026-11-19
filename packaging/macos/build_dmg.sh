#!/usr/bin/env bash
# Build the macOS disk image from PyInstaller output.
#   packaging/macos/build_dmg.sh 0.2.0 arm64
# Input:  dist/Topic Model Tool.app
# Output: dist/TopicModelTool-<version>-macOS-<arch>.dmg
#
# The app is ad-hoc signed (free, no Apple account). Apple Silicon refuses to run
# code with no signature at all, and macOS only offers "Open Anyway" in
# System Settings > Privacy & Security for signed apps.
set -euo pipefail
VERSION="${1:-0.2.0}"
ARCH="${2:-$(uname -m)}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$ROOT/dist/Topic Model Tool.app"
DMG="$ROOT/dist/TopicModelTool-$VERSION-macOS-$ARCH.dmg"
STAGE="$(mktemp -d)"

[ -d "$APP" ] || { echo "Missing $APP — run pyinstaller first"; exit 1; }

# Sign nested binaries first (the bundled engine and libraries), then the app.
find "$APP/Contents" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 |
  xargs -0 -I{} codesign --force --sign - --timestamp=none "{}" 2>/dev/null || true
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict "$APP"

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/docs/FIRST_OPEN.md" "$STAGE/How to open the first time.txt"

rm -f "$DMG"
hdiutil create -volname "Topic Model Tool" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
shasum -a 256 "$DMG"
