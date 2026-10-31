#!/usr/bin/env bash
# Builds a Linux AppImage of Comic Stitcher: one file that runs on most x86-64
# Linux desktops without installing. See packaging/appimage/README.md.
#
#   packaging/appimage/build_appimage.sh
#
# Run on Ubuntu 22.04 (its C library sets the oldest Linux the AppImage runs
# on), in a Python 3.14 environment installed from
# packaging/appimage/requirements-linux.txt with --require-hashes, as
# .github/workflows/appimage.yml does. Downloads UnRAR, cwebp and the AppImage
# tools recorded in packaging/appimage/tools.json, checking each one's SHA-256.
# Writes dist/ComicStitcher-<version>-x86_64.AppImage.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python}"
VERSION="$("$PYTHON" -c 'import comic_stitcher; print(comic_stitcher.__version__)')"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/ComicStitcher.AppDir"
OUTPUT="$ROOT/dist/ComicStitcher-$VERSION-x86_64.AppImage"
DOWNLOADS="$ROOT/build/appimage-downloads"  # Kept between builds; checked each time.
rm -rf "$WORK"
mkdir -p "$WORK" "$ROOT/dist"

echo "==> Fetching UnRAR, cwebp and the AppImage tools"
"$PYTHON" packaging/appimage/fetch_tools.py "$DOWNLOADS"

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
# Leave out system libraries every desktop has, and that must match the rest of
# the system: with the build machine's older GLib bundled, the desktop's own
# GLib plugins (GVfs, GTK's) fail to load ("undefined symbol"). Newer versions
# of these keep working with programs built against older ones. The same goes
# for D-Bus and the font libraries, which read the system's own settings.
echo "Left out, to use the system's own:"
find "$APP" -type f \( -name 'libglib-2.0.so*' -o -name 'libgobject-2.0.so*' -o -name 'libgio-2.0.so*' \
    -o -name 'libgmodule-2.0.so*' -o -name 'libgthread-2.0.so*' -o -name 'libdbus-1.so*' \
    -o -name 'libfontconfig.so*' -o -name 'libfreetype.so*' \) -print -delete
# Qt's GTK plugin brought the build machine's GTK with it, and that older GTK
# misread newer desktops' themes: no dark mode, faint check boxes. Without it
# Qt asks the desktop portal instead (AppRun chooses it), which also gives the
# desktop's own file dialogs; so the GTK libraries go too.
echo "Left out with Qt's GTK plugin:"
find "$APP" -type f -name 'libqgtk3.so' -print -delete
GTK_ONLY='libgtk-3 libgdk-3 libgdk_pixbuf-2.0 libatk-1.0 libatk-bridge-2.0 libatspi libcairo libcairo-gobject
libpango-1.0 libpangocairo-1.0 libpangoft2-1.0 libharfbuzz libfribidi libthai libdatrie libgraphite2
libpixman-1 libepoxy libXcomposite libXdamage libXi libXinerama libXcursor libXrandr'
for name in $GTK_ONLY; do
    find "$APP" -type f -name "$name.so*" -print -delete
done
# Nothing left may still need what was taken out with the GTK plugin.
stray=""
for file in $(find "$APP" -type f -name '*.so*'); do
    for needed in $(readelf -d "$file" 2>/dev/null | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p'); do
        for name in $GTK_ONLY; do
            case "$needed" in "$name".so*) stray="$stray ${file#"$APP"/} needs $needed;" ;; esac
        done
    done
done
if [ -n "$stray" ]; then
    echo "Still needed by what's bundled:$stray"
    if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::error title=GTK libraries still needed::$stray"; fi
    exit 1
fi
echo "System libraries bundled (the rest come from the system):"
find "$APP/_internal" -maxdepth 1 -name 'lib*.so*' -printf '  %f\n' | sort
# The documents the app opens (the User Guide on F1) and its licences.
mkdir -p "$APP/docs" "$APP/licenses"
cp README.md LICENSE "$APP/"
cp docs/*.md "$APP/docs/"
# UnRAR (CBR import) and cwebp (sharp color conversion), where the app looks first.
cp "$DOWNLOADS/unrar" "$DOWNLOADS/cwebp" "$APP/"
echo "==> Collecting the licence notices"
"$PYTHON" packaging/appimage/collect_notices.py "$APP" "$DOWNLOADS"
cp packaging/appimage/AppRun "$APPDIR/AppRun"
cp packaging/appimage/comic-stitcher.desktop "$APPDIR/comic-stitcher.desktop"
cp assets/app/comic-stitcher.png "$APPDIR/comic-stitcher.png"
ln -s comic-stitcher.png "$APPDIR/.DirIcon"

echo "==> Making the AppImage"
ARCH=x86_64 "$DOWNLOADS/appimagetool" --appimage-extract-and-run --no-appstream \
    --runtime-file "$DOWNLOADS/runtime-x86_64" "$APPDIR" "$OUTPUT"
ls -l "$OUTPUT"
