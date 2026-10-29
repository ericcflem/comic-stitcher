# Comic Stitcher 0.12.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **Center the art.** Printed comics often sit off-center, pushed aside by the
  margin kept for the binding. While you review pairs, Comic Stitcher checks
  every page in the background and, when pages are off-center, offers to move
  the art to the middle. It measures from the panels, so a sound effect
  spilling past one doesn't throw it off. Most pages come out well centered;
  **C** steps through them so you can check, and any page can be left as it was.
- **CBR export** with your own RAR program (WinRAR's `Rar.exe`, or `rar` on
  Linux), found automatically or chosen in Settings. RAR4, for older comic
  readers, needs RAR 6 or older.
- **Remove a joined spread** in one step in the final review.
- **Overlapping halves** found by the scan are reviewed with the Likely pairs.
- **Drop tab colors:** eleven swatches or any color you pick, solid and edged
  so the tab no longer disappears against a white window.

The full list is in [CHANGELOG.md](CHANGELOG.md).

The Windows x64 installer includes the required application dependencies; a
separate Python installation is not needed. Setup offers the Microsoft Visual C++
x64 prerequisite if required, under Microsoft's component terms. The installer's
Python libraries are unchanged from 0.6.38, and it includes Google's cwebp
encoder (libwebp 1.6.0, unchanged from 0.8.0) for WebP's sharp color conversion.

## Limitations

- Page-pair suggestions require human review. Missed pairs can be joined manually.
- Western and Manga presets do not guarantee page order; check the final review.
- Saved page-review sessions do not resume interrupted exports or retain an entire
  queued batch after closing the application.
- WebP and CB7 support varies between comic readers.
- PDF import preserves embedded JPEG pages when they are compatible; other PDFs
  are rendered. PDF export holds JPEG and PNG pages, not WebP, and has no
  ComicInfo.xml.
- Windows has an installer; Linux installs from source. There is no Mac or
  portable download. Comic Stitcher is not a comic reader or library manager.

See the [User Guide](USER_GUIDE.md) for instructions and
[SOURCE_ACCESS.md](SOURCE_ACCESS.md) for matching source, licenses and library
replacement information.
