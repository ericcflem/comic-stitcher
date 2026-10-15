# Changelog

## Unreleased

- Linux: started from the app menu, Comic Stitcher now tells the desktop it has
  finished starting, so the busy cursor stops once the window appears instead of
  spinning for about 15 seconds.
- PDF import: original JPEGs are now offered for PDFs made by TCPDF (such as
  Humanoids' books), which add a page number below the page, a hairline along its
  edge and an invisible link. Each JPEG is still extracted byte for byte.
- PDF import: when the original JPEGs can't be extracted, the Import PDF window
  says why, for example "Page 1: the JPEG is CMYK.", and the reason is logged.
- PDF import: the suggested rendering resolution now follows the page artwork.
  Lettering layers (often at a higher resolution) and a minority of larger scans
  no longer push it up, so the suggestion doesn't enlarge most of the book. The
  log lists what each sampled page measured.

## 0.7.0

**Linux**

- Install on Linux from the source download with `linux/install.sh`: Comic
  Stitcher gets its own private Python environment, an app-menu entry, Open With
  for comic files, Open in Comic Stitcher for folders in Files, and an
  uninstaller (`--purge` also removes settings and a remembered ComicVine key).
  The installer checks for and offers to install the system libraries it needs.
- If a system upgrade moves to a new Python version, Comic Stitcher offers to
  repair itself the next time it starts.
- RAR/CBR import uses the system's RAR extractor, and the ComicVine key is kept
  in the desktop keyring.

**New**

- An optional drop tab on the right screen edge (Windows and Linux): drag comics
  onto it to open them without arranging windows. It starts at sign-in when
  turned on (the Windows installer's option is ticked by default), can be hidden
  and brought back, and is removed on uninstall.
- ComicInfo.xml now always includes a page list that marks the front cover and
  two-page spreads (joined spreads and pages that are already wide), so library
  apps show the right cover and display spreads correctly.
- When importing a PDF whose pages are images, the rendering resolution is set to
  match them, with a note saying so. A resolution you choose yourself is kept.
- The export screen estimates the finished comic's size as settings change, by
  encoding bands from pages spread through the book in parallel. An early
  estimate appears within moments and is refined. Pages copied unchanged are
  counted exactly. It can be turned off with the Estimate size checkbox.
- Output options has one tab per copy, Full size and Reduced size, each with a
  checkbox to make that copy and its own format settings directly under its
  format choice. The reduced copy always has its own settings (starting from
  what it used before); at least one copy is always made.

**Fixed**

- PDFs made by Adobe Acrobat's image conversion are offered original JPEG
  extraction again. Acrobat adds an empty form to them, which no longer blocks it.
- The start, progress and export screens can no longer be resized smaller than
  their contents, which made text and buttons overlap. The progress window is
  wide enough for its messages from the start and makes room for long names.

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
