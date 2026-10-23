# Comic Stitcher 0.9.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **A simpler batch queue.** A comic joins the queue as soon as its Create your
  comic screen opens, and changes made there update it. Drag comics to change
  the export order, double-click one to change its settings, and view the queue
  from the Create your comic screen at any time. A single comic still exports
  straight away.
- **Faster spread suggestions.** Large books are scanned on several processor
  cores at once, several times faster, with identical suggestions.
- **More pages that just work.** 16-bit greyscale scans no longer turn white,
  CMYK pages convert with their own colour profile and can be saved as PNG, and
  pages whose names differ only in capitalisation (`001.jpg` and `001.JPG`) are
  kept in order instead of refused.
- **WebP for every page.** Large, finely detailed pages no longer stop a WebP
  export at the fastest compression efforts.
- **One worker setting.** A single Worker processes count in Settings now covers
  scanning, PDF rendering, screenshots and encoding.
- **Tidier windows.** Buttons and drop-down lists have consistent heights, and
  dialog buttons are in the Windows order on Linux too.

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

See [README.md](../README.md) for instructions and
[SOURCE_ACCESS.md](SOURCE_ACCESS.md) for matching source, licenses and library
replacement information.
