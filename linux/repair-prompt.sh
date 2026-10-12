#!/bin/sh
# Shown by Comic Stitcher's launcher when its private Python environment no longer
# works, which usually means a system upgrade moved to a new version of Python.
# Offers to re-run the installer kept in the install folder, which rebuilds it.
# Copied into place by install.sh; the launcher runs it as: sh repair-prompt.sh

HERE=$(cd "$(dirname "$0")" && pwd)
INSTALLER="$HERE/install.sh"
TITLE="Comic Stitcher needs a repair"
MESSAGE="Comic Stitcher needs a quick repair, usually after a system upgrade that moved to a new version of Python.

Repairing downloads its libraries again (about 120 MB), so it needs an internet connection. It may also ask for your password if a system package is missing."
COMMAND="bash \"$INSTALLER\""

# For anyone who started Comic Stitcher from a terminal.
printf '%s\n\n%s\n\nTo repair, run: %s\n' "$TITLE" "$MESSAGE" "$COMMAND" >&2

# 0 = repair, 1 = not now, 2 = no dialog could be shown.
ask() {
    [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] || return 2
    if command -v zenity >/dev/null 2>&1; then
        zenity --question --title="$TITLE" --width=440 --text="$MESSAGE" \
            --ok-label="Repair now" --cancel-label="Not now" 2>/dev/null
        case $? in 0) return 0 ;; 1) return 1 ;; esac
    fi
    if command -v kdialog >/dev/null 2>&1; then
        kdialog --title "$TITLE" --yes-label "Repair now" --no-label "Not now" \
            --yesno "$MESSAGE" 2>/dev/null
        case $? in 0) return 0 ;; 1) return 1 ;; esac
    fi
    return 2
}

# Open a terminal window running the installer; it waits for Enter when done.
run_in_terminal() {
    for terminal in xdg-terminal-exec ptyxis kgx gnome-terminal konsole xfce4-terminal \
                    x-terminal-emulator xterm; do
        command -v "$terminal" >/dev/null 2>&1 || continue
        case "$terminal" in
            xdg-terminal-exec)        "$terminal" bash "$INSTALLER" & ;;
            ptyxis|kgx|gnome-terminal) "$terminal" -- bash "$INSTALLER" & ;;
            xfce4-terminal)           "$terminal" -x bash "$INSTALLER" & ;;
            *)                        "$terminal" -e bash "$INSTALLER" & ;;
        esac
        return 0
    done
    return 1
}

tell() {
    [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] || return 0  # Already printed above.
    if command -v zenity >/dev/null 2>&1; then
        zenity --info --title="$TITLE" --width=440 --text="$1" 2>/dev/null && return
    fi
    if command -v kdialog >/dev/null 2>&1; then
        kdialog --title "$TITLE" --msgbox "$1" 2>/dev/null && return
    fi
    if command -v notify-send >/dev/null 2>&1; then
        notify-send --app-name="Comic Stitcher" "$TITLE" "$1" 2>/dev/null
    fi
}

ask
case $? in
    0)
        if ! run_in_terminal; then
            tell "No terminal program was found. Open a terminal and run:

$COMMAND"
        fi
        ;;
    1) ;;
    *) tell "Open a terminal and run:

$COMMAND" ;;
esac
exit 1  # Comic Stitcher itself did not start.
