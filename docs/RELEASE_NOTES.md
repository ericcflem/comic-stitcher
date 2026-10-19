# Comic Stitcher 0.8.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **Export to PDF.** Choose PDF as the archive format and pick JPEG or PNG for
  its pages. JPEG pages go in exactly as encoded. PDF is kept until Comic
  Stitcher closes, then your usual archive format returns.
- **Better spread suggestions.** A new seam test follows line art that crosses
  the seam at an angle and ignores the slicing debris often left at a split
  page's edge. In testing, every real spread was suggested and false
  suggestions fell by about two thirds. Plain borders in any colour, and thin
  white frames, are now recognised as page edges.
- **Sharp color conversion for WebP.** An option that keeps edges between
  strong colors clean in lossy WebP, at about twice the encoding time.
- **PDF import.** Original JPEGs can be extracted from more PDFs, including
  TCPDF-made books such as Humanoids'. When they can't be, the Import PDF window
  says why. The suggested rendering resolution follows the page artwork, so it
  no longer enlarges most of the book.
- **Linux.** The busy cursor stops as soon as the window appears.

The full list is in [CHANGELOG.md](CHANGELOG.md).

The Windows x64 installer includes the required application dependencies; a
separate Python installation is not needed. Setup offers the Microsoft Visual C++
x64 prerequisite if required, under Microsoft's component terms. The installer's
Python libraries are unchanged from 0.6.38; it now also includes Google's cwebp
encoder (libwebp 1.6.0) for WebP's sharp color conversion.

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
