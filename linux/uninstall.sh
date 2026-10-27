#!/usr/bin/env bash
# Remove Comic Stitcher installed by linux/install.sh.
#
#   uninstall.sh            Remove the program; keep your settings and logs.
#   uninstall.sh --purge    Also remove settings, logs and a remembered ComicVine key.
#   uninstall.sh --yes      Don't ask for confirmation.
#
# A copy is kept at ~/.local/share/comic-stitcher/uninstall.sh, so the
# downloaded folder is not needed. System packages the installer added (such as
# python3-venv) are left in place, since other programs may use them.
set -euo pipefail

APP_ID="comic-stitcher"
MARKER="Comic Stitcher install.sh"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PREFIX="$DATA_HOME/$APP_ID"
APP="$PREFIX/app"
VENV="$PREFIX/venv"
LAUNCHER="$PREFIX/$APP_ID"
BIN_LINK="$HOME/.local/bin/$APP_ID"
DESKTOP_FILE="$DATA_HOME/applications/$APP_ID.desktop"
DROP_TAB_DESKTOP_FILE="$DATA_HOME/applications/$APP_ID-drop-tab.desktop"
DROP_TAB_AUTOSTART="$CONFIG_HOME/autostart/$APP_ID-drop-tab.desktop"
ICON_ROOT="$DATA_HOME/icons/hicolor"
ICON_SIZES=(256 128 64 48)
NAUTILUS_EXTENSION="$DATA_HOME/nautilus-python/extensions/$APP_ID-folder-menu.py"
NAUTILUS_SCRIPT="$DATA_HOME/nautilus/scripts/Open in Comic Stitcher"
USER_DATA="$DATA_HOME/ComicStitcher"      # Logs and other working files.
USER_SETTINGS="$CONFIG_HOME/ComicStitcher"
PURGE=0
ASSUME_YES=0

say() { printf '%s\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

# Show a desktop notification, if the desktop offers them.
notify() {
    if command -v notify-send >/dev/null 2>&1; then
        timeout 5 notify-send --app-name="Comic Stitcher" --icon=comic-stitcher "$1" "$2" >/dev/null 2>&1 || true
    fi
}

# When the script ends, wait for Enter so a terminal window that closes on exit
# (as when the script is started from Files) can't hide the result or an error.
# shellcheck disable=SC2317  # Called by the EXIT trap.
pause_before_closing() {
    local status=$?
    if [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ] && [ -t 1 ]; then
        printf '\n'
        read -r -p "Press Enter to finish. " _ || true
    fi
    exit "$status"
}

ask() {
    local question=$1 reply
    if [ "$ASSUME_YES" -eq 1 ]; then return 0; fi
    [ -t 0 ] || die "Run this in a terminal, or add --yes."
    read -r -p "$question [y/N] " reply || reply=""
    case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# Remove a file only if the installer wrote it.
remove_if_ours() {
    if [ -f "$1" ] && grep -qF "$MARKER" "$1"; then rm -f "$1"; say "Removed $1"; fi
}

forget_comicvine_key() {
    if [ ! -x "$VENV/bin/python" ] || [ ! -d "$APP/comic_stitcher" ]; then return 0; fi
    "$VENV/bin/python" - "$APP" <<'PY' || true
import sys
sys.path.insert(0, sys.argv[1])
from comic_stitcher.key_store import KEYRING_MARKER, forget_key
forget_key(KEYRING_MARKER)
PY
}

main() {
    trap pause_before_closing EXIT
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --purge) PURGE=1 ;;
            -y|--yes) ASSUME_YES=1 ;;
            -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
            *) die "Unknown option: $1 (try --help)" ;;
        esac
        shift
    done
    [ "$(id -u)" -ne 0 ] || die "Run this as yourself, not with sudo."
    case "$PREFIX" in */"$APP_ID") ;; *) die "Unexpected install folder: $PREFIX" ;; esac

    if [ "$PURGE" -eq 1 ]; then
        ask "Remove Comic Stitcher, its settings, logs and any remembered ComicVine key?" || exit 0
    else
        ask "Remove Comic Stitcher? (Your settings and logs are kept.)" || exit 0
    fi

    if [ "$PURGE" -eq 1 ]; then forget_comicvine_key; fi

    # Close a running drop tab and remove its sign-in entry.
    if [ -x "$LAUNCHER" ]; then
        "$LAUNCHER" --uninstall-drop-tab >/dev/null 2>&1 || true
    fi
    if [ -f "$DROP_TAB_AUTOSTART" ] && grep -qF "$PREFIX" "$DROP_TAB_AUTOSTART"; then
        rm -f "$DROP_TAB_AUTOSTART"
    fi

    remove_if_ours "$DESKTOP_FILE"
    remove_if_ours "$DROP_TAB_DESKTOP_FILE"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true
    fi
    local size
    for size in "${ICON_SIZES[@]}"; do
        rm -f "$ICON_ROOT/${size}x${size}/apps/$APP_ID.png"
    done
    remove_if_ours "$NAUTILUS_EXTENSION"
    remove_if_ours "$NAUTILUS_SCRIPT"
    if [ -L "$BIN_LINK" ] && [ "$(readlink "$BIN_LINK")" = "$LAUNCHER" ]; then
        rm -f "$BIN_LINK"
    fi
    if [ -d "$PREFIX" ]; then
        rm -rf "$PREFIX"
        say "Removed $PREFIX"
    fi

    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$USER_DATA" "$USER_SETTINGS"
        say "Removed settings and logs."
    else
        say "Kept your settings ($USER_SETTINGS) and logs ($USER_DATA)."
        say "Run with --purge to remove those too."
    fi
    printf '\n==> %s\n' "Comic Stitcher was removed successfully."
    if [ "$PURGE" -eq 1 ]; then
        notify "Comic Stitcher was removed" "Its settings and logs were removed too."
    else
        notify "Comic Stitcher was removed" "Your settings were kept, in case you reinstall."
    fi
}

main "$@"
