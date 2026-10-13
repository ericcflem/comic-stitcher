# Changelog

## Unreleased

- Add an optional drop tab on the right screen edge: drag comics onto it to open
  them in Comic Stitcher without arranging windows. It can start at sign-in, is
  offered as an unticked installer option, and is closed and removed on uninstall.
- ComicInfo.xml now always includes a page list that marks the front cover and
  two-page spreads (joined spreads and pages that are already wide), so library
  apps show the right cover and display spreads correctly.
- When importing a PDF whose pages are images, the rendering resolution is set to
  match them, with a note saying so. A resolution you choose yourself is kept.
- PDFs made by Adobe Acrobat's image conversion are offered original JPEG
  extraction again. Acrobat adds an empty form to them, which no longer blocks it.
- The export screen estimates the finished comic's size as settings change, by
  encoding bands from pages spread through the book in parallel. An early
  estimate appears within moments and is refined. Pages copied unchanged are
  counted exactly. It can be turned off with the Estimate size checkbox.
- Export to PDF: choose PDF as the archive format. JPEG pages are stored byte for
  byte and PNG pages keep their compressed data, so nothing is re-encoded. PDF
  can't hold WebP, so the two can't be chosen together; copied WebP source pages
  are converted to the joined-page format. Pages are sized at 300 dpi and EXIF
  orientation is honoured. ComicInfo.xml isn't included in PDFs.

## 0.6.38 — first public release

- Import PDFs, supported comic archives, page-image folders and centered screenshots
  through a shared drop zone or separate file and folder browse buttons.
- Review suggested spread pairs and inspect seams; join, split, rotate and reorder
  pages in final review.
- Convert to JPEG, PNG or WebP and export CBZ, CB7 or PNG page folders, including
  full-size and reduced copies.
- Review and export batches, with options to remove comics or add more before export.
- Save page-review sessions and optionally edit ComicInfo.xml or use ComicVine lookup.
- Provide adjustable processing workers and export settings. Initial preferences use
  300 DPI for rendered PDF imports, JPEG quality 90, archive/book-name page prefixes
  and optional ComicInfo.xml creation turned off.
- Supply a Windows x64 installer, component notices, matching selected library
  sources, and application build and library replacement instructions.

Original input files are preserved. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for
release scope and limitations, and [README.md](../README.md) for the user guide.
