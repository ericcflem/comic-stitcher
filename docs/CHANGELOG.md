# Changelog

## Unreleased

- Fixed: the lossless WebP suggestion could stay hidden after changing the
  format or quality on Create your comic. The quick check made during review
  compared lossless with your usual settings, and when lossless lost there, the
  full comparison was skipped even for settings where it wins (WebP at 95: 168
  MB, against 37 MB lossless). The check's answer now applies only to the
  settings it measured.
- Queue window: dragging a comic shows the same pink dotted line as the final
  review's page strip, between comics and also above the first and below the
  last (Windows showed no line above the first). The comic lands where the line
  is.
- Fixed: after an export, measuring a screenshot folder (or checking a PDF's
  images) still showed **Process another comic** and **Export this comic
  again**. The first went back to the start screen while the work went on
  unseen. Those buttons are now cleared whenever new work starts, and do nothing
  while work is running.
- Tighter windows. The start screen drops the large "Comic Stitcher" heading
  (the title bar names it) and the queue text; the settings gear sits at the
  bottom left with the queue buttons, and the window is smaller. Settings, and
  the progress screens for importing, rendering and creating a comic, are just
  as tall as their contents. Create your comic can no longer be dragged so
  narrow that it cuts off its fields.
- Pages worth a look are pointed out after import: where the file numbering
  skips ("File numbers jump from 014.jpg to 016.jpg: a page may be missing
  between them"), and names that differ only in capitalisation, each with its
  page numbers and what to do about it. In the final review those pages are
  marked with a pink !, with the note in their tooltip, and **M** goes from one
  marked page to the next (unusual-width screenshots too). Books numbered in
  other ways (by twos, or with unnumbered pages) say nothing.
- Final review: dragging a page to either end of the thumbnail strip keeps it
  scrolling, faster the closer to the end, so a page can go from the front of a
  long book to the back in one drag.
- Create your comic: **Also save cover image** saves the front cover, exactly as
  it is in the comic, beside it ("Book - Cover.jpg"; one for each size), and
  **Also save ComicInfo.xml** saves the comic's ComicInfo.xml beside the archive
  as well as inside it. Beside a PDF, which has no place for it, that's the only
  copy; with no archive, the page folders already hold it.

## 0.10.0

**Screenshot import**

- The measurements summary is short and specific: how many screenshots have
  pages of each size (1988 × 3056, say), how many were measured from clear page
  edges and how many have black borders, the covers measured at their own size,
  and a note when there's more than one page size. No warning icon.
- Covers are set apart: a first or last screenshot a little narrower than the
  pages (1969 against 1988, say) or wider (some DC collections) is cropped at its
  own size and doesn't set the book's page size.
- Black-bordered pages, whose border is the same black as the screenshot, no
  longer make page sizes of their own: a page whose top or bottom rows are black
  is treated as black-bordered, whatever its sides measure (Fire Power's 1909 and
  1913 beside 1988). A black-bordered page whose art fits the book's one page
  size is cropped at it without review.
- A crop never cuts off art the screenshot shows. In a book with two page sizes
  (Marvel's 1988 × 3056 and 2063 × 3131 fitted as 2014, say), a black-bordered
  page takes a size whose crop holds all its art: the one a visible edge shows;
  the only one that fits; that of the clearly measured pages around it when they
  agree (a page whose art proves its size counts); for spreads, the one whose
  outer black border matches that size's other black-bordered spreads; else the
  size measured on more pages. Compression blur past a crop is allowed up to 6
  pixels.
- A single screenshot measured wider than the book's page size is cropped at
  its own size instead of setting every page's crop, and the summary notes that
  it may have been scaled down.
- In crop review, the page size is chosen with radio buttons, each showing how
  many screenshots measured at it. **Use this size for all pages** crops the
  whole book at the chosen size, and **This page's own size** measures one
  screenshot again on its own. Pages at their own size (covers), crops adjusted
  by hand and pages whose art the chosen size would cut keep their crop, and a
  message says which.

**Spreads and seams**

- Overlapping halves are suggested: the import scan looks for a strip of art
  repeated on both pages of a neighbouring pair, even where the edges don't
  match. Pair review offers it as **Possible · overlapping halves**, drawn lined
  up; **Yes** joins it over the repeated strip. A thin plain strip (up to 8 px)
  on one inner edge only, such as a white border added to one half, is covered
  by the other page when the pair is joined. Each one found is noted in the
  diagnostic log and the pair review report (`overlapping_halves`).
- **Seam** always opens the line-up tools, showing the seam as the pages will be
  joined: in pair review (**Use this alignment**), on a spread in the final
  review, and on two selected pages there (**Join as spread**). **Find overlap**
  measures a repeated strip; I and O move the halves a pixel together or apart;
  ← and → (or the Left/Right switch, or a click on a half) choose the page on
  top; Enter shows or hides a line where that page ends; Space finishes and Esc
  cancels. The overlap is kept with the spread for export, the size estimate,
  saved reviews and undo.

**Import**

- Pair review can be skipped: **None — the pages are already complete** reads
  the pages without looking for spreads and goes straight to the final review.
  Converting a CBR to CBZ takes an import, a look at the pages and an export.
  The choice resets to Suggested pairs for each comic.
- The **Right-to-left page order** checkbox is now **Back-to-front page order
  (manga)**, which says what it expects. It works as before.
- Linux: CBR/RAR comics in the newer RAR5 format. Comic Stitcher tries each
  installed extractor in turn (UnRAR, unar, bsdtar, 7-Zip), and if none can read
  the archive, recommends UnRAR or 7-Zip's RAR support (`7zip-rar`).
  `linux/install.sh` offers to install UnRAR.
- CMYK pages without a colour profile are converted as printed for US coated web
  offset (SWOP, CGATS TR 001), the usual conditions for North American comics,
  instead of with the plain formula: skin tones and colour casts come out right
  (on a test page, average ΔE 8.5 to 4.4 against the publisher's RGB version).
  The profile is public domain (CC0) and built in.

**Export**

- No archive saves the finished pages in a folder in the format you chose (JPEG,
  WebP or PNG; one folder for each size, such as "Book - Full - JPEG pages" and
  "Book - Reduced - WebP pages"), instead of only lossless PNG pages. **Also keep
  lossless PNG pages** adds PNG folders beside whatever you make; it's grayed out
  when, with no archive, the pages are already PNG.
- Create your comic suggests lossless WebP when it would make the comic three
  quarters of the size or less, with a **Use lossless WebP** button for that
  comic only. Clean line art, such as black-and-white strips rendered from a
  PDF, is often several times smaller this way with no loss at all. A quick
  check runs in the background during review, so the note is usually there when
  Create your comic opens. It applies to CBZ and CB7 with JPEG or lossy WebP
  pages.

**Windows**

- Uninstalling asks whether to remove your settings too: preferences, a
  remembered ComicVine API key, saved reviews, pair review reports and logs.
  They are kept unless you choose Yes.

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
