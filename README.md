# Comic Stitcher

Comic Stitcher is an easy-to-use application designed to turn a messy source into a clean comic book file. Comic Stitcher is a comic book preparation tool, not a reader or library manager.

It imports PDFs, image folders, compressed archives, existing comic book files and web screenshots, and allows you to stitch together split 2-page spreads, rotate pages, and clean up layouts without ever modifying your original source files.

The [User Guide](docs/USER_GUIDE.md) explains each step and setting.

**About development:** Comic Stitcher was coded by AI, with Eric Fleming directing its design, testing it, and guiding revisions throughout extensive development. It began with a spread-detection script Eric developed with Grok; ChatGPT/Codex and Claude helped turn it into the application. Gemini helped refine this README and, alongside Grok, contributed to the application icon, which Eric edited manually.

---

## Quick Start

1. **Import:** Drag comic files or folders onto the window, or use **Choose files…** / **Choose folder…**.
2. **Choose:** Pick how to review spreads (suggested pairs, every pair, or none) and the page order.
3. **Pair review:** Confirm which neighboring pages are halves of a two-page spread.
4. **Final review:** Check the whole comic; join, split, reorder, rotate or remove pages.
5. **Export:** Choose the name, format and size, then create your comic.

See the [User Guide](docs/USER_GUIDE.md) for the details of each step.

---

## Input Formats & Import Options

Comic Stitcher imports:

- **PDFs**, rendered at a chosen resolution, or with their original JPEG pages extracted unchanged when possible.
- **Comic archives:** `CBZ`, `CB7` and `CBR`, and plain ZIP, 7z and RAR files.
- **Folders of page images:** `JPEG`, `PNG`, `WebP`, `BMP` and `TIFF`.
- **Folders of screenshots** from a reading app or emulator, with black borders at the sides, which are measured, cropped and split into pages.

It exports `CBZ`, `CB7`, `PDF` (and `CBR`, with your own RAR program), or a folder of pages, in JPEG, WebP or PNG, at full size, reduced size, or both.

- **Batches:** Drop several comics, or a folder of them, to review each in turn and export them all from a queue.
- **Drop tab:** An optional strip on the screen's edge to drop comics onto, without finding the window first.
- **Resuming:** Your review is saved as you go; open the same comic again to pick up where you left off.

---

## Installation & Requirements

- **Windows:** The official Windows installer includes the Python runtime and all necessary dependencies. No external installations of ImageMagick, Ghostscript, Poppler, or FFmpeg are required.
- **UnRAR:** RAR/CBR import relies on the companion UnRAR utility bundled with the installer. Exporting to CBR uses your own WinRAR, if you have it, since only RARLAB's software may create RAR archives.
- **cwebp:** WebP's sharp color conversion uses Google's `cwebp.exe` from libwebp, bundled with the installer.
- **System Dependencies:** If needed, the installer will prompt to install the Microsoft Visual C++ x64 runtime.
- **Uninstalling:** Remove Comic Stitcher from **Settings > Apps** or **Control Panel**. It then asks whether to remove your settings, logs and a remembered ComicVine key too; choose **No** to keep them for a reinstall.
- **Linux:** Install from the source download with `linux/install.sh` (see [Installing on Linux](#installing-on-linux)).

### Installing on Linux

1. From the [latest release](https://github.com/ericcflem/comic-stitcher/releases/latest), download **Source code (zip)** and extract it. (**Code > Download ZIP** on the main page also works, but gives the newest, possibly unreleased, code.)
2. Open a terminal in the extracted folder and run:

   ```bash
   linux/install.sh
   ```

   Run it as yourself, not with `sudo`. If a system package is missing, such as `python3-venv` on Ubuntu, it lists what it needs and asks before installing it, which needs your password. Comic Stitcher's own libraries go in a private folder in your home folder, about 120 MB to download the first time.
3. Start Comic Stitcher from your app menu, or right-click a comic and choose **Open With > Comic Stitcher**. In Files, right-click a folder and choose **Open in Comic Stitcher**. If Files' Python add-on support is not installed, this is under **Scripts** instead.

You can delete the downloaded folder afterwards. To update, download the new version and run its `linux/install.sh` again. If a system upgrade moves to a new version of Python, Comic Stitcher offers to repair itself the next time it starts. To uninstall, run `~/.local/share/comic-stitcher/uninstall.sh`. Add `--purge` to also remove your settings, logs and a remembered ComicVine key.

Notes:
- **RAR/CBR import** uses an installed RAR extractor: UnRAR first, then unar, bsdtar or 7-Zip. `linux/install.sh` offers to install UnRAR (`sudo apt install unrar` on Ubuntu or Debian). Use UnRAR, or 7-Zip with its RAR support (`sudo apt install 7zip-rar`), for comics in the newer RAR5 format: unar, bsdtar, and 7-Zip without its RAR support, can't read them fully.
- **Sharp color conversion for WebP** uses libwebp's `cwebp` tool. `linux/install.sh` offers to install it; on Ubuntu or Debian it is `sudo apt install webp`. Without it the option is greyed out.
- **A remembered ComicVine key** is kept in the desktop keyring, such as GNOME Keyring or KDE Wallet.
- **On GNOME's Wayland desktop,** Comic Stitcher runs through XWayland, so GNOME draws its window frame and shadow. To use native Wayland instead, set `COMIC_STITCHER_NATIVE_WAYLAND=1`. You might want this if text looks blurry with fractional display scaling.

*For developer build instructions, see [BUILDING.md](docs/BUILDING.md). For version history, see [CHANGELOG.md](docs/CHANGELOG.md).*

---

## License & Credits

- **License:** Comic Stitcher's own material uses the [MIT License](LICENSE). See [PROVENANCE.md](docs/PROVENANCE.md) for development history and attribution.
- **Icons & Artwork:** Page icons created by Eric Fleming. Application icon created with AI assistance and manual editing. UI icons via Phosphor Icons (MIT).
- **Third-Party Libraries:** Built using Qt/PySide6, Pillow, NumPy, pypdfium2, py7zr, and rarfile. Third-party components retain their respective licenses (see `licenses/` directory for full terms).
Matching library sources and license information are described in [SOURCE_ACCESS.md](docs/SOURCE_ACCESS.md). Developers can find library rebuild and replacement instructions in [REBUILDING_AND_REPLACING_LIBRARIES.md](docs/REBUILDING_AND_REPLACING_LIBRARIES.md).

