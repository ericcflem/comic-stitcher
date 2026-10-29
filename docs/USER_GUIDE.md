# Comic Stitcher User Guide

This guide explains each step of making a comic with Comic Stitcher, and what each
setting does. For installation, see the [README](../README.md).

Most settings also explain themselves: rest the mouse on one to see a tooltip.

**Your originals are never changed.** Cropping, joining, rotating and converting all
make new files. Keep your originals until you have checked the results.

## Contents

1. [The steps at a glance](#the-steps-at-a-glance)
2. [Importing](#importing)
3. [Screenshots and cropping](#screenshots-and-cropping)
4. [Pair review](#pair-review)
5. [Final review](#final-review)
6. [Export](#export)
7. [Output options](#output-options)
8. [Metadata and ComicVine](#metadata-and-comicvine)
9. [Batches and the queue](#batches-and-the-queue)
10. [The drop tab](#the-drop-tab)
11. [Settings](#settings)
12. [Keyboard shortcuts](#keyboard-shortcuts)
13. [Where things are saved](#where-things-are-saved)

## The steps at a glance

1. **Import** a PDF, comic archive, folder of page images or folder of screenshots.
2. **Crop** screenshots, if you imported them.
3. **Pair review:** confirm which neighboring pages are two halves of one spread.
4. **Final review:** check the whole comic; join, split, reorder, rotate or remove pages.
5. **Export:** choose names, formats and sizes, then create the comic.

## Importing

Drag files or folders onto the start screen, or use **Choose files…** or **Choose
folder…**. Comic Stitcher reads:

- **PDFs**
- **Comic archives:** CBZ, CB7 and CBR, and plain ZIP, 7z and RAR files
- **Folders of page images:** JPEG, PNG, WebP, BMP and TIFF
- **Folders of screenshots** with black borders at the sides, such as from a reading app or
  emulator (see [Screenshots and cropping](#screenshots-and-cropping))

A folder is read **one level deep**: the images directly inside it are the pages. A
folder holding several comic files or page folders is treated as a batch (see
[Batches and the queue](#batches-and-the-queue)).

### Start comic import

Before importing, Comic Stitcher asks how to treat the comic:

- **Folder contains** (folders only): **Comic page images**, or **Emulator screenshots —
  crop and split**. Comic Stitcher samples the images and suggests one.
- **Pair review:**
  - **Suggested pairs** compares page edges and shows the pairs most likely to be
    spreads, as Likely and Possible.
  - **Manual — every neighboring pair** shows every pair in turn, without scoring them.
  - **None — the pages are already complete** skips pair review and goes straight to
    the final review. Use it for comics whose spreads are already joined, such as when
    converting a CBR to CBZ. You can still join, remove or rotate pages there.

  This resets to Suggested pairs for each comic.
- **Back-to-front page order (manga):** use this when the front cover is the last page
  in the source. Spreads are checked and joined right to left, and the finished comic
  is still numbered from the front cover.

### PDFs

If each page of a PDF is a single JPEG image, Comic Stitcher offers **Extract original
JPEGs**. This takes the images out exactly as they are, with no loss of quality. Choose
**Render to PNG instead** to draw the pages, which you'd want for a PDF with text or
drawings on top of its images.

When a PDF is rendered, you choose:

- **Rendering resolution:** higher keeps more detail for fine text and line art, but
  takes longer and uses more memory and disk space. The default is 300 dpi (about
  1988 × 3056 pixels for a standard U.S. comic page). When the PDF's pages are images,
  it's set to match them; going higher makes bigger pages without adding detail.
- **Smooth embedded images:** on by default. Turning it off gives harder edges, which
  can look jagged.

### Pages to check

After importing, Comic Stitcher may say some places are worth a look, such as pages
whose names differ only in capitalisation. These pages are marked with a pink **!** in
the final review. Press **M** there to go from one to the next.

### Resuming a review

Your review is saved as you go. If you open the same comic again, unchanged, Comic
Stitcher offers **Resume review**, which brings back your decisions and joins, or
**Start over**.

## Screenshots and cropping

For screenshots, Comic Stitcher measures each one to find the page between the black
borders at its left and right, then shows a summary: the page sizes it found, how many
screenshots have each, and any that need your choice.

The page should fill the screenshot from top to bottom, with black borders only at
the sides, as when a portrait page is shown on a wide screen. Black borders above and
below the page aren't cropped.

**Review crop previews** opens the crop review. Each screenshot shows its crop outline:

- **Yellow:** a single page.
- **Blue:** a two-page spread, which is split into two pages.
- **Red:** a sideways two-page spread.

A screenshot marked **Review** needs you to choose **Single**, **Double** or
**Sideways**. You can also:

- **Page size:** choose which of the measured page sizes this screenshot uses, or
  **This page's own size** for a page of a different size, such as a cover. **Use this
  size for all pages** applies the size everywhere, except to pages measured at their
  own size and crops you adjusted by hand.
- **Adjust crop…:** move the crop edges by hand.
- **Replace screenshot…:** swap in a better screenshot, for example if a spread was
  captured sideways and should be retaken.
- **Book single-page size:** set the page size yourself if no page could be measured.

**Approve crops and continue** crops the screenshots and goes on to pair review. Use
Left and Right to move between screenshots.

## Pair review

Comic Stitcher shows two neighboring pages side by side. Decide whether they are two
halves of one spread:

- **Approve** (Y or Up) joins them.
- **Reject** (N or Down) keeps them as separate pages.
- **Revisit previous decision** (Left or Backspace) goes back one pair.

With **Suggested pairs**, the Likely pairs come first. When they're done, choose
**Review Possible** to check the lower-scoring pairs too, or **Continue to Final
Preview** to leave them unjoined. You can still join any pages in the final review.

With **Manual** review, you step through every neighboring pair: 1–2, then 3–4 if
those were joined, or 2–3 if not.

Other controls:

- **Rotate left / Rotate right** turns the spread, if it was scanned sideways.
- **Inspect seam** (Space) shows a close-up of where the halves meet. Up and Down
  scroll along it.
- **Line up spread** is for spreads where both halves repeat a strip of the art at the
  seam, or one half has a thin border there. Move the halves together until the art
  lines up (I for in, O for out), or use **Find overlap**. **Page on top** chooses which
  half covers the other where they overlap; put the half with the clean edge on top.

If two pages have different heights, the taller one is trimmed at the bottom when they
are joined.

## Final review

The final review shows the finished comic, with every page in the filmstrip.

- **Move between pages** with Left and Right, or click a thumbnail.
- **Select two pages:** click one, then Shift-click its neighbor.
- **Join as Spread** joins two selected pages. **Split Back Into Pages** separates a
  joined spread.
- **Swap selected pages** swaps two selected neighbors.
- **Reorder** by dragging a thumbnail to the left or right half of another. A pink
  dotted line shows where it will go.
- **Rotate page** turns the current page left or right.
- **Remove current page…** leaves the page out, after asking. On a joined spread, it
  removes both of its pages.
- **Undo last edit** undoes the last move, swap, rotation, join, split or removal.
- **Inspect selected seam** (Space) shows how two selected pages would meet as a spread,
  or lets you line up the halves of a joined spread.
- **Marked pages:** pages marked with a pink **!** are worth a check. Press **M** to go
  to the next one.
- **Back to Pair Review** returns to the pair decisions, including any Possible pairs
  you skipped.

When the comic looks right, choose **Continue to Export**.

### Centering the art

Printed comics often sit off-center on the page: the art is pushed to one side by the
wider margin kept for the binding, left on one page and right on the next. While you
review pairs, Comic Stitcher measures where the art sits on each page, in the
background. If some pages are off-center, Comic Stitcher asks, once, whether to center
the art, and the final review keeps a highlighted line saying how many pages, with
**Center the art**. If the check is still running when you choose **Continue to
Export**, it waits for it first (**Skip the check** goes on without it):

- Checked, those pages are shown, and exported, with their art moved sideways to the
  middle. The strip it uncovers is filled with the page's own color, and the page
  keeps its size.
- Press **C** to go from one off-center page to the next. **Center this page**, shown
  on each, leaves that page as it was when unchecked. **Undo last edit** undoes either.
- The art is measured from the outermost panel sides, so a sound effect spilling past a
  panel moves with the art without throwing the measurement off. Art is never moved
  off the page.
- Only white or black pages are centered, and only single pages: never a spread, art
  that runs to the page edges, or a page on a colored background.

Most pages will be better centered, but a few may not come out exactly right, so it's
worth stepping through them with **C**. Moved pages are saved again at export, in the
**Format for joined/rotated pages** (PNG unless you change it, so nothing is lost).
To skip the check altogether, uncheck it in [Settings](#settings).

## Export

The **Create your comic** screen sets how the finished comic is named and saved.

**Book**

- **Book name:** the name of the finished comic file and of any folders saved beside
  it. When you make both a full-size and a reduced copy, their names end in Full and
  Reduced.
- **Filename style:** how the pages inside are named: **Book name - number** (Book
  Title - 001.jpg), **Number only** (001.jpg) or **Custom prefix - number**, which lets
  the page names differ from the book name.
- **Numbering:** **Start at 0 (cover)** or **Start at 1**. A joined spread counts as
  one page.
- **Number padding:** how many digits each page number has, such as 3 for 001.
  **Automatic** fits the number of pages.

**Output**

- **Image handling:**
  - **Copy unmodified pages without re-encoding** keeps unchanged pages exactly as they
    were. Only joined spreads and rotated pages are saved again.
  - **Re-encode all final pages** saves every page in the chosen format.
- **Archive format:**
  - **CBZ:** the most widely supported comic format.
  - **CB7:** a 7z comic archive; check that your comic reader opens it.
  - **CBR:** a RAR comic archive, offered when you have RARLAB's RAR program (see
    below).
  - **PDF:** one image per page. A PDF holds JPEG or PNG pages, so you're asked which.
    PDF stays chosen until Comic Stitcher closes, then your usual format returns.
  - **No archive:** saves the finished pages in a folder.

  RAR is a closed format: only RARLAB's own WinRAR and RAR programs may create RAR
  archives, and the UnRAR tool Comic Stitcher uses can only open them. So Comic
  Stitcher makes a CBR with your own copy: `Rar.exe`, which comes with WinRAR, on
  Windows, or RARLAB's `rar` on Linux (`sudo apt install rar` on Ubuntu). It finds
  it where it's usually installed, or you can choose it in [Settings](#settings).
  WinRAR is a trial, then paid, from RARLAB. Without it, CBR isn't in the list; CBZ
  is the usual replacement, and comic readers that open CBR open CBZ too.

  CBRs are made in the RAR5 format. If your comic reader can't open one, check
  **Use the older RAR4 format**, below CBR. RAR 7 and later make only RAR5, so this
  needs RAR or WinRAR 6 or older.
- **Save comic in front-to-back page order** (back-to-front comics only): checked, the
  comic starts with the front cover; unchecked, it keeps the back-to-front order.
- **Also keep lossless PNG pages:** also saves the final pages as PNG files in a folder,
  before they're compressed for the comic.
- **Also save cover image:** saves the front cover beside the comic (Book - Cover.jpg).
- **Include ComicInfo.xml** adds the comic's details for library apps; **Edit
  metadata…** changes them (see [Metadata and ComicVine](#metadata-and-comicvine)).
  **Also save ComicInfo.xml** saves a copy beside the comic too.
- **Edit options…** opens the [output options](#output-options): image format,
  quality and sizes.
- **Estimate size** shows about how large the finished comic will be, and updates when
  you change a setting.
- **Use lossless WebP:** shown when lossless WebP would make this comic much smaller
  with no loss of quality, as it often does for black-and-white line art. It applies to
  this comic only. Check that your comic reader opens WebP.
- **Keep imported pages for faster queued export:** see [Batches and the
  queue](#batches-and-the-queue).

**Start export** creates the comic. It's saved beside the original PDF or archive, or
beside the folder of images. Nothing is overwritten: if the name is taken, the new
comic gets a different name.

When it's done, **Export this comic again** goes back to the export settings to make
another version, and **Process another comic** goes back to the start screen.

## Output options

**Edit options…** on the export screen opens these. Your choices are remembered for
the next comic.

### Full-size copy

**Create a full-size copy** keeps the pages at their original size.

- **Output format:** JPEG, WebP or PNG.
  - **JPEG** is the most widely supported.
  - **WebP** makes smaller files at the same quality, and can be lossless. Not every
    comic reader opens it.
  - **PNG** is lossless, but files are large.
- **Format for joined/rotated pages** (when unmodified pages are copied): the format for
  pages that have to be saved again. PNG avoids losing any quality; JPEG or WebP is
  smaller.
- **JPEG quality / WebP quality:** 1–100; higher keeps more detail but makes larger
  files. The default is 90.

Advanced settings for each format:

- **JPEG**
  - **Progressive:** saves in several passes, so a reader can show a rough version
    while it loads.
  - **Optimize Huffman tables:** slightly smaller files with the same image; takes
    longer.
  - **Chroma subsampling:** how much color detail is kept. 4:4:4 keeps it all, which
    helps colored lettering. 4:2:2 and 4:2:0 make smaller files but can soften colored
    edges.
  - **Smoothing:** 0–100. It can hide noise, but blurs fine lines and lettering. Zero
    leaves it off.
- **WebP**
  - **Lossless WebP:** keeps every pixel exactly. Quality then sets compression effort
    rather than image quality.
  - **Compression effort:** 0–6. Higher takes longer to find a smaller file.
  - **Sharp color conversion (sharp YUV):** keeps edges between strong colors, such as
    red lettering on black, clean instead of blurred or darkened. It takes about twice
    as long. On Linux this needs the `webp` package.
- **PNG**
  - **Compression effort (lossless):** 0–9. Higher makes smaller files more slowly. The
    image is the same at every level. The default is 6.

**Reset to defaults** puts the encoder settings and qualities back to their defaults.

### Reduced-size copy

**Create a reduced-size copy** makes a smaller version, for a phone or tablet, say. You
can make it instead of, or as well as, the full-size copy.

- **Resize by:** **Height** or **Width**, and the **Target dimension** in pixels. The
  default is 1920 pixels high. Pages are never enlarged, and joined spreads keep their
  shape.
- **Gamma corrected resize:** blends colors more accurately while resizing, so bright
  details don't darken. It takes longer.
- **Resampling:** **Bilinear** is fastest but softer; **Bicubic** balances smoothness
  and detail; **Lanczos** looks sharpest but can show faint halos at strong edges.
- **Output format** and quality, with the same advanced settings as the full-size copy.

## Metadata and ComicVine

**Edit metadata…** opens the comic's details: series, issue number, title, writers,
publisher and so on. Only checked fields are included. Details already in the comic are
kept unless you change or uncheck them.

**Look up on ComicVine…** searches ComicVine for the comic and fills in its details.
This needs your own free ComicVine API key, entered in [Settings](#settings). Find the
series, choose the issue or collected edition, and review the fields before choosing
**Use these fields**. **Replace populated metadata fields** decides whether ComicVine's
details replace ones already filled in, or only fill empty fields.

## Batches and the queue

Drop several comics, or a folder of comics, to review them as a batch. You review each
comic in turn, and they wait in the queue; the exports run one after another when you
start the queue.

- **View queue…** shows the queued comics. Drag one to change the order, or select it
  and press Ctrl with Up, Down, Home or End. Double-click a comic to change its export
  settings. **Remove selected** takes a comic out of the queue; its original is
  untouched.
- **Add comic files…** and **Add folder…** add more comics.
- **Start export** (or **Start queue**) creates the comics, from the top down.

When you start a batch, Comic Stitcher asks how to store the imported pages:

- **Clear temporary pages** (recommended) uses less disk space. PDFs and archives are
  imported again when the queue runs, so it takes longer.
- **Keep for faster export** keeps every comic's imported pages until it's exported.
  A large queue can use a lot of disk space.

If the queue stops on an error, that comic and the rest stay queued. Fix the problem
and start the queue again.

## The drop tab

The drop tab is a slim strip on the right edge of the screen. Drag a comic, PDF or
folder onto it and it slides out; let go, and the comic opens in Comic Stitcher, or
joins the window that's already open.

- **Turn it on:** on Windows, the installer's **Show a drop tab on the right edge of the
  screen** option is ticked by default. On Linux, `linux/install.sh` turns it on for a
  new install (add `--no-drop-tab` to skip that). **Comic Stitcher Drop Tab** in the
  Start menu or app menu starts it again. From source, run `python launch.py
  --drop-tab`.
- **Use it:** rest the mouse on the tab, or click it, to see what it is; click the open
  panel to start Comic Stitcher. Drag the tab up or down to move it. Right-click it, or
  its tray icon, for **Start when I sign in**, **Hide drop tab** and **Close drop tab**.
- **Adjust it:** right-click and choose **Drop tab settings…** to set how far it sticks
  out, its height, its distance from the top, the hover delay, and its color: one of
  eleven swatches (blue by default), or any color with **Custom…**.
- **Get it back:** click the tray icon, or use the **Comic Stitcher Drop Tab** shortcut.
- On Linux's Wayland desktops, the tab runs through XWayland, because Wayland doesn't
  let ordinary windows stay at a screen edge.

## Settings

The gear on the start screen opens Settings:

- **Worker processes:** how many pages are worked on at once. **Automatic** uses about
  half the computer's processors. Fewer keeps the computer more responsive; more
  finishes sooner if there's enough memory.
- **Show the original-file notice at startup.**
- **Check whether each page's art is centered:** measures each comic's pages in the
  background while you review pairs, so the final review can offer to [center the
  art](#centering-the-art). Uncheck it if you never want that.
- **Your ComicVine API key,** for ComicVine lookups. **Remember** keeps it, protected by
  Windows or by your desktop keyring on Linux. The key is never put in comics or logs.
- **Optional CBR export:** the RAR program Comic Stitcher found for making CBRs, or
  **Browse…** to choose one (on Windows, `Rar.exe` in WinRAR's folder). **Find
  automatically** goes back to looking for it.
- **Open diagnostic logs** and **Open pair review reports** open their folders.
- **About and credits…**

## Keyboard shortcuts

| Where | Key | Action |
| :--- | :--- | :--- |
| Pair review | Y or Up | Approve the pair |
| | N or Down | Reject the pair |
| | Left or Backspace | Go back one pair |
| | Space | Inspect the seam (Up / Down scroll it) |
| Line up spread | I / O | Move the halves together / apart |
| | Left / Right | Choose the page on top |
| | Enter | Show or hide the seam line |
| | Space / Esc | Done / Cancel |
| Final review | Left / Right | Previous / next page |
| | Space | Inspect the seam of two selected pages |
| | M | Next page marked with ! |
| | C | Next off-center page |
| Crop review | Left / Right | Previous / next screenshot |
| Queue | Ctrl + Up / Down / Home / End | Move the selected comic |

## Where things are saved

- **Finished comics:** beside the original PDF or archive, or beside the folder of
  images or screenshots.
- **Diagnostic logs:** `%LOCALAPPDATA%\ComicStitcher\logs` on Windows,
  `~/.local/share/ComicStitcher/logs` on Linux. Open them from **Settings → Open
  diagnostic logs**.
- **Pair review reports:** in a `review-reports` folder next to the logs. Open them
  from **Settings → Open pair review reports**.
