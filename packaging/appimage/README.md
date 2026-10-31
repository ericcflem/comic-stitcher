# Linux AppImage

A second way to run Comic Stitcher on Linux, beside `linux/install.sh`: one file,
`ComicStitcher-<version>-x86_64.AppImage`, that runs without installing anything.

- `build_appimage.sh` freezes Comic Stitcher with PyInstaller (as the Windows
  build does), arranges it as an AppDir with `AppRun` and the desktop entry, and
  makes the AppImage with appimagetool.
- `requirements-linux.txt`: the libraries, at the Windows release's versions,
  as official Linux wheels checked by hash.
- `tools.json` and `fetch_tools.py`: UnRAR (from RARLAB's RAR for Linux; only
  `unrar` is taken), Google's `cwebp`, appimagetool and the AppImage runtime,
  each checked against its recorded SHA-256.
- `collect_notices.py` writes the `licenses/` folder and its index (the About
  window's "View third-party notices"): the Python packages', Qt's, Python's,
  the tools' and the runtime's notices, and the copyright file of each Ubuntu
  library the AppImage carries. A bundled library without a known source stops
  the build.
- `.github/workflows/appimage.yml` builds it on Ubuntu 22.04 (the oldest Linux
  it runs on is set by the C library it's built against), starts it on a
  virtual screen, checks that a copy started from the drop tab runs without the
  system's X11 libraries, and keeps it as a download on the run's page. When a
  release is published, it builds the AppImage from the release's tag, attaches
  it, and adds its line to the release's `SHA256SUMS.txt`.

Inside, it differs from an installed copy in a few ways:

- Qt's GTK plugin, and the build machine's GTK, are left out: an older GTK
  misreads newer desktops' themes. `AppRun` has Qt use the desktop's settings
  portal instead (light or dark, and the desktop's own file dialogs).
- So are the system libraries every desktop has and that must match the rest
  of it (GLib, D-Bus, fontconfig, FreeType).
- Programs it starts (the file manager, a browser) get the system's own
  libraries, not its copies (`launch.py`).

Desktop integration (`comic_stitcher/appimage.py`): on first start the AppImage
asks once whether to add itself to the app menu (with its icon, the drop tab's
entry, Open With for comics, and Open in Comic Stitcher in the Files folder
menu), and Settings has Add to app menu / Remove from app menu. Removing can also
remove the settings, logs and saved reviews. Every entry has `TryExec`, so it
hides itself once the AppImage is deleted, and the Files script removes itself;
if the AppImage is moved, the entries follow it at its next start.
