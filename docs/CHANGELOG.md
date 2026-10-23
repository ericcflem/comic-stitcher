# Changelog

## Unreleased

- Pair review can be skipped: choose **None — the pages are already complete**
  for Pair review when importing. The pages are read without looking for
  spreads, and the import goes straight to the final review, where pages can
  still be joined, removed or rotated. Converting a CBR to CBZ, with copied pages
  and optional ComicInfo.xml, takes an import, a look at the pages and an
  export. The choice resets to Suggested pairs for each comic.

## 0.9.0

**Batch queue**

- A comic joins the queue as soon as its Create your comic screen
  opens, with the settings shown; changing them updates its queue entry. The
  separate Review final output and Ready to queue steps are gone. The blue
  button reads **Review next comic** while dropped comics are waiting and
  **Start export** otherwise; a single comic exports straight away and can still
  be exported again. **View queue (N)** is on that screen whenever comics are
  queued, with **Add another comic**, and double-clicking a queued comic opens
  its settings to change them.
- Drag a comic up or down to change the order comics are exported
  in, or select it and press Ctrl with Up, Down, Home or End.

**Import and spread suggestions**

- Scanning large books for spread suggestions now spreads the pages across worker
  processes, as export does (half the processor's threads, up to six), so it
  finishes several times faster on multi-core computers. Small books, under
  about 40 MB of pages, still scan in one process, which is quicker for them.
  The suggestions are identical either way.
- Pages whose names differ only in capitalisation (`comic - 001.jpg` and
  `comic - 001.JPG`), which Linux and comic archives allow, are no longer
  refused. They are kept in the order Linux file managers and comic readers
  show them (capitalisation ignored, lowercase first), and a note before review
  names them and says if any are identical copies. Archives extract them
  without one overwriting the other, on Windows too. The same image path twice
  in one archive is still refused.
- Copied pages with upper-case extensions (`.JPG`) are saved with lowercase
  ones, like every other page, and the file pickers on Linux show archives,
  PDFs and images with upper-case extensions.
- 16-bit greyscale pages (from some scanners' PNG and TIFF files) are now scaled
  to 8 bits instead of clipped. Before, anything lighter than nearly black came
  out white in JPEG, WebP, PDF and reduced copies, in previews, and to the spread
  scanner. Full-size PNG copies of them are now 8-bit like every other page.

**Export**

- CMYK pages can be saved as PNG; before, the export stopped with "cannot write
  mode CMYK as PNG". CMYK pages with an embedded colour profile are converted to
  RGB with that profile, so their colours match how they were meant to print;
  without one, the previous conversion is used.
- WebP: large, finely detailed pages (halftone scans, for example) no
  longer stop the export with "PARTITION0_OVERFLOW" at the fastest compression
  efforts (0-2). libwebp keeps the first part of a lossy WebP under 512 KB, and
  only measures it as it goes from effort 3. Such a page is now encoded again at
  effort 3, then, if needed, with libwebp's limits relaxed. A page that still
  can't fit gets a message suggesting JPEG, PNG or lossless WebP.

**Settings and appearance**

- One **Worker processes** setting in Settings replaces the three separate counts
  (PDF rendering, screenshot, encoding). It covers scanning, PDF rendering,
  screenshot measuring and cropping, and encoding. Automatic is unchanged: about
  half the processor's threads, up to six, and up to four for PDF rendering. An
  existing encoding count carries over.
- Dialog buttons are in the same order on Linux as on Windows. Linux desktops
  reordered them, putting Cancel in the middle of some dialogs.
- Buttons and drop-down lists are consistent heights. Plain buttons are all the
  standard height (those in the queue and import windows, and the Yes and No of
  warnings opened from the queue, were as tall as the blue ones); blue buttons
  share one slightly taller height; drop-down lists are one height everywhere.

## 0.8.0

**New**

- Export to PDF: choose PDF in the Archive format list. A window then asks for
  the pages' format, JPEG or PNG, with the usual settings for each and for the
  full-size and reduced copies. JPEG pages are stored byte for byte and PNG
  pages keep their compressed data. Copied pages that aren't JPEG or PNG are
  converted to the chosen format. PDF and its page settings are kept until Comic
  Stitcher closes, then your usual archive format and output options return;
  they are never saved over them. ComicInfo.xml isn't included in a PDF.
- WebP export: new **Sharp color conversion (sharp YUV)** option under the WebP
  encoder settings. Edges between strong colors, such as red lettering or blue
  sky against black ink, stay clean instead of bleeding or darkening. Encoding
  takes about twice as long and files are a few percent larger, so it is off by
  default. Windows builds include Google's `cwebp` encoder for it; on Linux,
  `linux/install.sh` offers to install it.

**Spread suggestions**

- New seam test. It skips the slicing debris often found at a split page's edge
  (a copy of the neighbouring page's last column, or a blank strip), lets lines
  land a little higher or lower across the seam so diagonal line art counts as
  continuing, and compares the seam with each page's own texture instead of with
  fixed limits. Blank page edges and plain paper margins no longer look like
  perfect matches. The previous pixel comparison remains as a fallback for very
  confident matches. Tested on 1,042 pages from four books (84 real spreads):
  every spread is now suggested (83 as likely, previously 68, with 2 never
  suggested), and false suggestions fell from 17 to 6. A saved review of a
  comic that is scanned again (an archive or PDF) starts over, since the
  suggestions differ.
- A plain strip at least 4 px wide down a page's whole edge now counts as a
  border even when artwork starts inside the 14 px strip that is examined, so
  pages with thin white frames are no longer paired. A seam is also only judged
  when enough of its height (5%) has something to compare; before, a few
  agreeing patches could make two framed pages look like a perfect match.
- Plain page edges in any colour (teal, brown, tinted grey) now count as
  borders, as black, white and grey always did. Pages on matching coloured
  backgrounds are no longer suggested as spreads.

**PDF import**

- Original JPEGs are now offered for PDFs made by TCPDF (such as Humanoids'
  books), which add a page number below the page, a hairline along its edge and
  an invisible link. Each JPEG is still extracted byte for byte.
- When the original JPEGs can't be extracted, the Import PDF window says why,
  for example "Page 1: the JPEG is CMYK.", and the reason is logged.
- The suggested rendering resolution now follows the page artwork. Lettering
  layers (often at a higher resolution) and a minority of larger scans no longer
  push it up, so the suggestion doesn't enlarge most of the book. The log lists
  what each sampled page measured.

**Linux**

- Started from the app menu, Comic Stitcher now tells the desktop it has
  finished starting, so the busy cursor stops once the window appears instead of
  spinning for about 15 seconds.

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
