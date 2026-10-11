#!/usr/bin/env bash
# Install (or update) Comic Stitcher for the current user on Linux.
#
#   linux/install.sh          Install, or update an earlier install.
#   linux/install.sh --yes    Answer yes to every question (for unattended use).
#
# Run it from the downloaded Comic Stitcher folder, as yourself (not with sudo).
# Everything goes in your home folder; it asks for your password only if it
# needs to install a missing system package. Afterwards the downloaded folder
# can be deleted. To remove Comic Stitcher, run
#   ~/.local/share/comic-stitcher/uninstall.sh
set -euo pipefail

APP_ID="comic-stitcher"
MARKER="Comic Stitcher install.sh"   # Marks files uninstall.sh may remove.

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA_HOME/$APP_ID"
APP="$PREFIX/app"
VENV="$PREFIX/venv"
LAUNCHER="$PREFIX/$APP_ID"
BIN_LINK="$HOME/.local/bin/$APP_ID"
DESKTOP_FILE="$DATA_HOME/applications/$APP_ID.desktop"
ICON_ROOT="$DATA_HOME/icons/hicolor"
ICON_SIZES=(256 128 64 48)   # uninstall.sh removes the same sizes
NAUTILUS_EXTENSION="$DATA_HOME/nautilus-python/extensions/$APP_ID-folder-menu.py"
NAUTILUS_SCRIPT="$DATA_HOME/nautilus/scripts/Open in Comic Stitcher"
PYTHON="${PYTHON:-python3}"
ASSUME_YES=0

# File types offered under "Open With" (the same set the Windows installer uses).
MIME_TYPES="application/vnd.comicbook+zip;application/vnd.comicbook-rar;application/x-cb7;application/pdf;application/zip;application/vnd.rar;application/x-7z-compressed;"

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'Note: %s\n' "$*" >&2; }
die()  { printf '\nError: %s\n' "$*" >&2; exit 1; }

# ask "Question?" Y|N  -> succeeds for yes.
ask() {
    local question=$1 default=$2 reply
    if [ "$ASSUME_YES" -eq 1 ]; then return 0; fi
    if [ ! -t 0 ]; then [ "$default" = Y ]; return; fi
    if [ "$default" = Y ]; then question="$question [Y/n] "; else question="$question [y/N] "; fi
    read -r -p "$question" reply || reply=""
    case "${reply:-$default}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# Quote a value for a POSIX shell script.
sh_quote() { printf "'%s'" "${1//\'/\'\\\'\'}"; }

# Quote an argument for a desktop file's Exec= line (Desktop Entry spec).
desktop_quote() {
    local value=$1
    if [[ $value =~ ^[A-Za-z0-9_./+@%,:=-]+$ ]]; then printf '%s' "$value"; return; fi
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//\`/\\\`}
    value=${value//\$/\\\$}
    value=${value//\\/\\\\}   # The value is also unescaped once as a string.
    printf '"%s"' "$value"
}

# Write a template with @LAUNCHER@ filled in, safely quoted for its language.
fill_template() {
    local template=$1 destination=$2 language=$3
    "$PYTHON" - "$template" "$destination" "$language" "$LAUNCHER" <<'PY'
import shlex, sys
template, destination, language, launcher = sys.argv[1:]
text = open(template, encoding="utf-8").read()
if language == "python":
    text = text.replace('"@LAUNCHER@"', repr(launcher))
else:
    text = text.replace("'@LAUNCHER@'", shlex.quote(launcher))
if "@LAUNCHER@" in text:
    sys.exit("template placeholder not filled: " + template)
open(destination, "w", encoding="utf-8").write(text)
PY
}

package_manager() {
    local manager
    for manager in apt-get dnf pacman zypper; do
        if command -v "$manager" >/dev/null 2>&1; then printf '%s' "$manager"; return; fi
    done
}

# package_name MANAGER (venv|xcb-cursor|nautilus-python)
package_name() {
    case "$1:$2" in
        apt-get:venv)            printf 'python3-venv' ;;
        apt-get:xcb-cursor)      printf 'libxcb-cursor0' ;;
        apt-get:nautilus-python) printf 'python3-nautilus' ;;
        dnf:xcb-cursor)          printf 'xcb-util-cursor' ;;
        dnf:nautilus-python)     printf 'nautilus-python' ;;
        pacman:xcb-cursor)       printf 'xcb-util-cursor' ;;
        pacman:nautilus-python)  printf 'python-nautilus' ;;
        zypper:xcb-cursor)       printf 'libxcb-cursor0' ;;
    esac
}

install_packages() {
    local manager=$1; shift
    case "$manager" in
        apt-get) sudo apt-get install -y "$@" || { sudo apt-get update && sudo apt-get install -y "$@"; } ;;
        dnf)     sudo dnf install -y "$@" ;;
        pacman)  sudo pacman -S --needed --noconfirm "$@" ;;
        zypper)  sudo zypper --non-interactive install "$@" ;;
    esac
}

install_command() {
    local manager=$1; shift
    case "$manager" in
        apt-get) printf 'sudo apt install %s' "$*" ;;
        dnf)     printf 'sudo dnf install %s' "$*" ;;
        pacman)  printf 'sudo pacman -S %s' "$*" ;;
        zypper)  printf 'sudo zypper install %s' "$*" ;;
    esac
}

venv_works() {
    local probe status=0
    probe=$(mktemp -d)
    "$PYTHON" -m venv "$probe/venv" >/dev/null 2>&1 || status=1
    rm -rf "$probe"
    return "$status"
}

has_xcb_cursor() {
    local library
    for library in /usr/lib/libxcb-cursor.so.0 /usr/lib64/libxcb-cursor.so.0 \
                   /usr/lib/*/libxcb-cursor.so.0 /lib/*/libxcb-cursor.so.0; do
        [ -e "$library" ] && return 0
    done
    { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null; } | grep -q 'libxcb-cursor\.so\.0'
}

has_nautilus_python() {
    local module
    for module in /usr/lib/*/nautilus/extensions-*/libnautilus-python.so \
                  /usr/lib64/nautilus/extensions-*/libnautilus-python.so \
                  /usr/lib/nautilus/extensions-*/libnautilus-python.so; do
        [ -e "$module" ] && return 0
    done
    return 1
}

check_system() {
    step "Checking this computer"
    command -v "$PYTHON" >/dev/null 2>&1 || die "Python 3 was not found. Install it with your system's software tools, then run this again."
    "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
        || die "Comic Stitcher needs Python 3.10 or newer; this system has $("$PYTHON" -V 2>&1)."
    say "Found $("$PYTHON" -V 2>&1)."

    local manager needed=() reasons=() name
    manager=$(package_manager)

    if ! venv_works; then
        name=$(package_name "$manager" venv)
        [ -n "$name" ] || die "Python cannot create private environments here (the 'venv' module is missing). Install your system's Python venv package, then run this again."
        needed+=("$name"); reasons+=("$name: lets Comic Stitcher keep its own private copy of its libraries (required)")
    fi
    if ! has_xcb_cursor; then
        name=$(package_name "$manager" xcb-cursor)
        if [ -n "$name" ]; then
            needed+=("$name"); reasons+=("$name: gives Comic Stitcher the desktop's own window frame and shadows (recommended)")
        else
            warn "the libxcb-cursor library seems to be missing; Comic Stitcher will still run, without the desktop's window shadows."
        fi
    fi
    if command -v nautilus >/dev/null 2>&1 && ! has_nautilus_python; then
        name=$(package_name "$manager" nautilus-python)
        if [ -n "$name" ]; then
            needed+=("$name"); reasons+=("$name: puts 'Open in Comic Stitcher' directly in Files' right-click menu for folders (optional; otherwise it goes under Scripts)")
        fi
    fi

    [ "${#needed[@]}" -gt 0 ] || { say "Everything needed is already installed."; return; }

    say "These system packages are missing:"
    printf '  - %s\n' "${reasons[@]}"
    if ask "Install them now? This asks for your password." Y; then
        install_packages "$manager" "${needed[@]}" || warn "installing system packages did not finish."
    else
        say "Skipped. You can install them later with:"
        say "  $(install_command "$manager" "${needed[@]}")"
    fi
    venv_works || die "Python still cannot create private environments. Install $(package_name "$manager" venv) and run this again."
}

copy_app() {
    step "Copying Comic Stitcher to $APP"
    [ -f "$SOURCE/launch.py" ] && [ -d "$SOURCE/comic_builder" ] && [ -f "$SOURCE/requirements.txt" ] \
        || die "Run this script from inside the downloaded Comic Stitcher folder (it was run from $SOURCE)."
    mkdir -p "$PREFIX"
    local stage="$PREFIX/app.new"
    rm -rf "$stage" "$PREFIX/app.old"
    mkdir -p "$stage"
    local item
    for item in launch.py LICENSE README.md requirements.txt comic_builder assets docs linux; do
        [ -e "$SOURCE/$item" ] && cp -R "$SOURCE/$item" "$stage/"
    done
    find "$stage/comic_builder" \( -name 'test_*.py' -o -name '__pycache__' \) -prune -exec rm -rf {} +
    [ -d "$APP" ] && mv "$APP" "$PREFIX/app.old"
    mv "$stage" "$APP"
    rm -rf "$PREFIX/app.old"
    cp "$APP/linux/uninstall.sh" "$PREFIX/uninstall.sh"
    chmod +x "$PREFIX/uninstall.sh"
}

setup_environment() {
    step "Setting up Comic Stitcher's private Python environment"
    local system_version venv_version=""
    system_version=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    if [ -x "$VENV/bin/python" ]; then
        venv_version=$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
    fi
    if [ "$venv_version" != "$system_version" ]; then
        # New, broken, or made by an older Python (for example after a system upgrade).
        rm -rf "$VENV"
        "$PYTHON" -m venv "$VENV"
    fi

    # Comic Stitcher uses only the core of Qt (PySide6-Essentials), so skip the
    # large PySide6-Addons package that the PySide6 bundle would also download.
    local requirements="$PREFIX/requirements-installed.txt"
    sed -E 's/^[[:space:]]*PySide6[[:space:]]*==/PySide6-Essentials==/' "$APP/requirements.txt" > "$requirements"
    grep -q '^PySide6-Essentials==' "$requirements" \
        || die "requirements.txt no longer pins PySide6 as expected; the installer needs updating."

    say "Downloading and installing libraries (about 120 MB the first time; updates are quicker)..."
    "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$requirements"
}

write_launcher() {
    step "Creating the launcher"
    {
        printf '#!/bin/sh\n'
        printf '# Starts Comic Stitcher. Written by %s; re-run it to update.\n' "$MARKER"
        printf '# Matches the window to its app-menu entry on X11/XWayland desktops.\n'
        printf 'RESOURCE_NAME=%s\nexport RESOURCE_NAME\n' "$APP_ID"
        printf 'exec %s %s "$@"\n' "$(sh_quote "$VENV/bin/python")" "$(sh_quote "$APP/launch.py")"
    } > "$LAUNCHER"
    chmod +x "$LAUNCHER"

    mkdir -p "$(dirname "$BIN_LINK")"
    if [ -L "$BIN_LINK" ] || [ ! -e "$BIN_LINK" ]; then
        ln -sfn "$LAUNCHER" "$BIN_LINK"
    else
        warn "$BIN_LINK already exists and is not Comic Stitcher's; leaving it alone."
    fi
}

install_icons() {
    local icon_value=$APP_ID
    if ! "$VENV/bin/python" - "$APP/assets/app/comic-stitcher.png" "$ICON_ROOT" "$APP_ID" "${ICON_SIZES[@]}" <<'PY'
import sys
from pathlib import Path
from PIL import Image
source, root, name, *sizes = sys.argv[1:]
with Image.open(source) as image:
    image = image.convert("RGBA")
    for size in sizes:
        folder = Path(root) / f"{size}x{size}" / "apps"
        folder.mkdir(parents=True, exist_ok=True)
        image.resize((int(size), int(size)), Image.LANCZOS).save(folder / f"{name}.png")
PY
    then
        warn "could not prepare the menu icon; using the original image."
        icon_value="$APP/assets/app/comic-stitcher.png"
    fi
    printf '%s' "$icon_value"
}

write_desktop_entry() {
    step "Adding Comic Stitcher to the app menu and to Open With"
    local icon
    icon=$(install_icons)
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    if [ -f "$DESKTOP_FILE" ] && ! grep -q "X-ComicStitcher-Installer=" "$DESKTOP_FILE"; then
        say "Replacing an existing $APP_ID.desktop that was made by hand."
    fi
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Comic Stitcher
GenericName=Comic Book Preparation
Comment=Turn PDFs, image folders and comic archives into clean comic book files
Exec=$(desktop_quote "$LAUNCHER") %F
Icon=$icon
Terminal=false
Categories=Graphics;
Keywords=comic;cbz;cbr;cb7;pdf;manga;spread;stitch;
MimeType=$MIME_TYPES
StartupNotify=true
StartupWMClass=$APP_ID
X-ComicStitcher-Installer=$MARKER
EOF
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true
    fi
}

install_folder_menu() {
    step "Adding 'Open in Comic Stitcher' for folders"
    rm -f "$NAUTILUS_EXTENSION" "$NAUTILUS_SCRIPT"
    if ! command -v nautilus >/dev/null 2>&1; then
        say "Files (Nautilus) is not installed, so this step is skipped."
        say "Folders can still be dropped onto Comic Stitcher or chosen with 'Choose folder...'."
        return
    fi
    if has_nautilus_python; then
        mkdir -p "$(dirname "$NAUTILUS_EXTENSION")"
        fill_template "$APP/linux/nautilus/comic-stitcher-folder-menu.py" "$NAUTILUS_EXTENSION" python
        say "Right-click a folder in Files and choose 'Open in Comic Stitcher'."
    else
        mkdir -p "$(dirname "$NAUTILUS_SCRIPT")"
        fill_template "$APP/linux/nautilus/open-in-comic-stitcher.sh" "$NAUTILUS_SCRIPT" shell
        chmod +x "$NAUTILUS_SCRIPT"
        say "Right-click a folder in Files and choose Scripts > Open in Comic Stitcher."
    fi
    if pgrep -x nautilus >/dev/null 2>&1; then
        say "Close all Files windows once (or run: nautilus -q) for the menu item to appear."
    fi
}

main() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -y|--yes) ASSUME_YES=1 ;;
            -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
            *) die "Unknown option: $1 (try --help)" ;;
        esac
        shift
    done
    [ "$(id -u)" -ne 0 ] || die "Run this as yourself, not with sudo. It asks for your password only if it needs to install system packages."
    case "$(uname -s)" in Linux) ;; *) die "This installer is for Linux." ;; esac

    check_system
    copy_app
    setup_environment
    write_launcher
    write_desktop_entry
    install_folder_menu

    step "Done"
    say "Start Comic Stitcher from your app menu (search for \"Comic Stitcher\"),"
    say "or right-click a comic and choose Open With > Comic Stitcher."
    say "To update later, download the new version and run its linux/install.sh again."
    say "To uninstall: $PREFIX/uninstall.sh"
}

main "$@"
