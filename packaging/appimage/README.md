# Linux AppImage (trial)

A trial of a second way to run Comic Stitcher on Linux: one file,
`ComicStitcher-<version>-x86_64.AppImage`, that runs without installing
anything. `linux/install.sh` remains the supported way.

- `build_appimage.sh`: freezes Comic Stitcher with PyInstaller (as the Windows
  build does), arranges it as an AppDir with `AppRun` and the desktop entry, and
  makes the AppImage with appimagetool (whose runtime needs no libfuse2).
- `.github/workflows/appimage.yml`: builds it on Ubuntu 22.04 (the oldest Linux
  it should run on is set by the C library it's built against), starts it on a
  virtual screen, and keeps it as a download on the run's page.

Running it: download it, then `chmod +x ComicStitcher-*.AppImage` (or Properties →
Allow executing as program) and open it.

Not yet decided or done:

- Libraries: built from `requirements.txt`'s ranges, not hash-pinned wheels as the
  Windows release is.
- UnRAR and cwebp: taken from the system, as with `install.sh`.
- Desktop integration (app menu, Open With, the Files folder menu): none of its own.
- Licence notices: copied as they are; no generated index yet.
