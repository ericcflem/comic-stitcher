# Comic Stitcher

Comic Stitcher is an easy-to-use application designed to turn a messy source into a clean comic book file. Comic Stitcher is a comic book preparation tool, not a reader or library manager.

It imports PDFs, image folders, compressed archives, existing comic book files and web screenshots, and allows you to stitch together split 2-page spreads, rotate pages, and clean up layouts without ever modifying your original source files.

**About development:** Comic Stitcher was coded by AI, with Eric Fleming directing its design, testing it, and guiding revisions throughout extensive development. It began with a spread-detection script Eric developed with Grok; ChatGPT/Codex and Claude helped turn it into the application. Gemini helped refine this README and, alongside Grok, contributed to the application icon, which Eric edited manually.

---

## Quick Start

1. **Import:** Drag files or folders into the drop zone, or use **Choose files…** / **Choose folder…**.
2. **Scan:** Select your input type, page order (front-to-back or back-to-front), and spread detection settings.
3. **Review Pairs:** Inspect and confirm split spreads identified by the auto-scanner or step through manually.
4. **Final Edit:** Use the **Final Comic Review** canvas to check layout, rotate pages, or tweak joins.
5. **Export:** Configure resolution, output formats, and archive options, then export your final comic.

---

## Installation & Requirements

- **Windows:** The official Windows installer includes the Python runtime and all necessary dependencies. No external installations of ImageMagick, Ghostscript, Poppler, or FFmpeg are required.
- **UnRAR:** RAR/CBR import relies on the companion UnRAR utility bundled with the installer. Exporting to CBR/RAR is not supported.
- **System Dependencies:** If needed, the installer will prompt to install the Microsoft Visual C++ x64 runtime.
- **Linux:** Install from the source download with `linux/install.sh` (see [Installing on Linux](#installing-on-linux)). An AppImage is planned.

### Installing on Linux

1. Download Comic Stitcher's source (on GitHub: **Code > Download ZIP**) and extract it.
2. Open a terminal in the extracted folder and run:

   ```bash
   linux/install.sh
   ```

   Run it as yourself, not with `sudo`. If a system package is missing, such as `python3-venv` on Ubuntu, it lists what it needs and asks before installing it, which needs your password. Comic Stitcher's own libraries go in a private folder in your home folder, about 120 MB to download the first time.
3. Start Comic Stitcher from your app menu, or right-click a comic and choose **Open With > Comic Stitcher**. In Files, right-click a folder and choose **Open in Comic Stitcher**. If Files' Python add-on support is not installed, this is under **Scripts** instead.

You can delete the downloaded folder afterwards. To update, download the new version and run its `linux/install.sh` again. To uninstall, run `~/.local/share/comic-stitcher/uninstall.sh`. Add `--purge` to also remove your settings, logs and a remembered ComicVine key.

Notes:
- **RAR/CBR import** uses whichever RAR extractor is installed (UnRAR, unar, 7-Zip or bsdtar). On Ubuntu or Debian, `sudo apt install unar` adds one.
- **A remembered ComicVine key** is kept in the desktop keyring, such as GNOME Keyring or KDE Wallet.
- **On GNOME's Wayland desktop,** Comic Stitcher runs through XWayland, so GNOME draws its window frame and shadow. To use native Wayland instead, set `COMIC_STITCHER_NATIVE_WAYLAND=1`. You might want this if text looks blurry with fractional display scaling.

*For developer build instructions, see [BUILDING.md](docs/BUILDING.md). For version history, see [CHANGELOG.md](docs/CHANGELOG.md).*

---

## Input Formats & Import Options

Comic Stitcher supports PDFs, archives (`CBZ`, `CB7`, `CBR`), raw image folders (`JPEG`, `PNG`, `WebP`, `BMP`, `TIFF`), and screenshot sets.

### Batch Processing
Drop multiple comic files or folders onto the application window to review them as a batch. You can also drop a parent folder containing a mixture of comic files and page-image folders.

At the last comic's confirmation, start exports directly or choose **Review batch…** to check the output settings and destinations, remove an item, or add more comic files or a folder. Removing an item from the batch does not delete its source.
* **Folder Depth:** Comic Stitcher scans **one level deep**. Folders containing multiple comic folders work as expected, but nested sub-folders within individual comics will be ignored.
* **Resuming:** Unfinished review sessions are automatically saved as checkpoints. Reopening an unmodified source allows you to resume where you left off. This saves review progress, not the entire queued batch.
* **Memory Management:** By default, temporary extracted assets are cleaned up after each job in a batch to save space. Choose **Keep imported pages** when setting up a batch for faster queued exports if disk space permits.

### Drop Tab

The optional drop tab is a slim strip on the right edge of the screen, a little below the corner. Drag a comic, PDF or folder onto it and the tab slides out; let go and the comic opens in Comic Stitcher, or joins the window that is already open. You never need to arrange windows to reach the drop zone.

* **Turn it on:** tick **Show a drop tab on the right edge of the screen** in the installer, or run `ComicStitcher.exe --drop-tab` (`python launch.py --drop-tab` from source).
* **Use it:** rest the mouse on the tab for a second, or click it, to see what it is; click the open panel to start Comic Stitcher. Drag the tab up or down to move it, and right-click it (or its tray icon) to choose **Start when I sign in** or **Close drop tab**.
* **Linux:** on Wayland the tab runs through XWayland, because Wayland does not let ordinary windows stay pinned to a screen edge. Ubuntu may need the `libxcb-cursor0` package for that.

### PDF Processing
* **Lossless Extraction:** If a PDF contains compatible embedded JPEGs that can be extracted without re-encoding, Comic Stitcher offers direct extraction to preserve original quality.
* **Rendering:** If extraction isn't available, PDFs are rendered at a configurable DPI (default is **300 DPI**, yielding ~1988×3056 px for standard U.S. comic dimensions).

### Screenshots & Auto-Cropping
Designed for reading platforms or web captures bounded by dark/black borders:
* Automatically detects recurring page dimensions and proposes centered crops.
* Handles multiple aspect ratios within the same batch.
* Losslessly splits detected 2-page spread captures into single pages before sending them to the pair-review queue.
* **Rotated Spreads:** If a 2-page spread was captured sideways (e.g., orientation requiring 90° rotation), adjust the crop boundary manually or replace the screenshot before proceeding.

---

## Reviewing & Stitching Spreads

Comic Stitcher offers two primary ways to review candidate spread pairs:

* **Suggested Review:** Prioritizes high-confidence pairs ("Likely") followed by secondary matches ("Possible"). Because full-bleed artwork or dark borders can cause false positives/negatives, a quick pass is recommended.
* **Manual Review:** Steps sequentially through neighboring pairs (`1–2`, `3–4` if paired; `2–3` if not) for complete manual verification.

### Navigation Shortcuts
| Action | Key |
| :--- | :--- |
| **Approve Pair** | `Y` or `Up Arrow` |
| **Decline Pair** | `N` or `Down Arrow` |
| **Previous Pair** | `Left Arrow` or `Backspace` |
| **Toggle Seam View** | `Spacebar` |
| **Scroll Seam** | `Up` / `Down` (in Seam View) |

Space also toggles Seam View for two selected adjacent pages in Final Comic Review.

---

## Final Comic Review

The **Final Comic Review** stage provides a full overview of the issue layout prior to export.

* **Join & Split:** Click a thumbnail, then Shift-click its neighbor to select two pages for joining. To split a joined spread, select that spread and choose **Split**.
* **Page Ordering:** Drag thumbnails to reorder pages. Set **Right-to-Left Page Order** if working with back-to-front source materials.
* **Rotations & Deletions:** Rotate individual pages or remove unwanted pages (e.g., ads, blank pages) after confirmation.
* **Mismatch Handling:** Joining images of unequal height automatically clips excess height from the taller page's bottom margin.

---

## Exporting & Output Choices

Comic Stitcher offers flexible export pipelines for different devices or storage preferences:

* **Output Formats:** Export as `CBZ` (ZIP), `CB7` (7z), or a folder of processed images in PNG format.
* **Multi-Resolution Export:** Generate full-resolution archives, web/mobile optimized copies (e.g., fixed 1920px height downsampling), or both simultaneously.
* **Passthrough Optimization:** Unmodified pages can be copied without re-encoding to preserve exact source bytes.
* **File Naming:** Page names default to the archive name plus a padded number. Customize prefixes, zero-padding, and starting indices (`0` or `1`).

Saved preferences take priority over fresh defaults. Output is saved beside an input PDF/archive, or in the parent of an image/screenshot folder. The final confirmation shows the destinations; existing output is not overwritten.

---

## Metadata & Diagnostic Logs

* **Metadata Editing:** Edit or inject `ComicInfo.xml` tags prior to saving. Optional ComicVine lookup uses your own API key; review the results before accepting them.
* **Logs & Support:** Diagnostic logs are stored in `%LOCALAPPDATA%\ComicSpreadBuilder\logs` (accessible directly via **Settings → Open diagnostic logs**).

---

## License & Credits

- **License:** Comic Stitcher's own material uses the [MIT License](LICENSE). See [PROVENANCE.md](docs/PROVENANCE.md) for development history and attribution.
- **Icons & Artwork:** Page icons created by Eric Fleming. Application icon created with AI assistance and manual editing. UI icons via Phosphor Icons (MIT).
- **Third-Party Libraries:** Built using Qt/PySide6, Pillow, NumPy, pypdfium2, py7zr, and rarfile. Third-party components retain their respective licenses (see `licenses/` directory for full terms).
Matching library sources and license information are described in [SOURCE_ACCESS.md](docs/SOURCE_ACCESS.md). Developers can find library rebuild and replacement instructions in [REBUILDING_AND_REPLACING_LIBRARIES.md](docs/REBUILDING_AND_REPLACING_LIBRARIES.md).

