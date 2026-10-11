#!/bin/sh
# Files (Nautilus) script: right-click > Scripts > Open in Comic Stitcher.
# Installed by Comic Stitcher's linux/install.sh when Files' Python add-on support
# is not available; removed by uninstall.sh.
# INSTALLED_BY: Comic Stitcher install.sh

LAUNCHER='@LAUNCHER@'  # Filled in by install.sh.

[ -n "${NAUTILUS_SCRIPT_SELECTED_FILE_PATHS:-}" ] || exit 0

# Files gives one selected path per line.
set --
while IFS= read -r path; do
    [ -n "$path" ] && set -- "$@" "$path"
done <<EOF
$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
EOF

[ "$#" -gt 0 ] || exit 0
exec "$LAUNCHER" "$@"
