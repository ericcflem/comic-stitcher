#!/usr/bin/env bash
# Builds a Linux AppImage of Comic Stitcher: one file that runs on most x86-64
# Linux desktops without installing. (A trial: see packaging/appimage/README.md.)
#
#   packaging/appimage/build_appimage.sh
#
# Run in a Python 3.14 environment with requirements.txt and PyInstaller
# installed, on the oldest Linux it should run on (its C library sets the
# floor; GitHub's Ubuntu 22.04 machines are used). Writes
# dist/ComicStitcher-<version>-x86_64.AppImage.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python}"
VERSION="$("$PYTHON" -c 'import comic_stitcher; print(comic_stitcher.__version__)')"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/ComicStitcher.AppDir"
OUTPUT="$ROOT/dist/ComicStitcher-$VERSION-x86_64.AppImage"
rm -rf "$WORK"
mkdir -p "$WORK" "$ROOT/dist"

echo "==> Freezing Comic Stitcher $VERSION with PyInstaller"
"$PYTHON" -m PyInstaller --noconfirm --clean --windowed --onedir \
    --distpath "$WORK/dist" --workpath "$WORK/pyinstaller" --specpath "$WORK" \
    --paths "$ROOT" --additional-hooks-dir "$ROOT/packaging/build_hooks" \
    --exclude-module PySide6.QtVirtualKeyboard --collect-all pypdfium2 \
    --hidden-import PySide6.QtSvg --hidden-import launch \
    --add-data "$ROOT/assets/licenses/Phosphor-LICENSE.txt:." \
    --add-data "$ROOT/assets/app/comic-stitcher.png:." \
    --name comic-stitcher "$ROOT/launch.py"

echo "==> Arranging the AppImage's folder"
mkdir -p "$APPDIR/usr/lib"
mv "$WORK/dist/comic-stitcher" "$APPDIR/usr/lib/comic-stitcher"
APP="$APPDIR/usr/lib/comic-stitcher"
# The documents the app opens (the User Guide on F1) and its licences.
mkdir -p "$APP/docs" "$APP/licenses"
cp README.md LICENSE "$APP/"
cp docs/*.md "$APP/docs/"
cp -R assets/licenses/. "$APP/licenses/"
rm -rf "$APP/licenses/microsoft"  # The Windows runtime's terms; nothing of it is in here.
cp -R third_party_notices "$APP/licenses/python-packages"
cp packaging/appimage/AppRun "$APPDIR/AppRun"
cp packaging/appimage/comic-stitcher.desktop "$APPDIR/comic-stitcher.desktop"
cp assets/app/comic-stitcher.png "$APPDIR/comic-stitcher.png"
ln -s comic-stitcher.png "$APPDIR/.DirIcon"

echo "==> Making the AppImage"
TOOL="$WORK/appimagetool"
curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x "$TOOL"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR" "$OUTPUT"
ls -l "$OUTPUT"
