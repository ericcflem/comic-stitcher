# Comic Stitcher 0.10.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **Smarter screenshot crops.** Covers of a different size are cropped on their
  own, black-bordered pages no longer create false page sizes, and a crop never
  cuts off art the screenshot shows. Books with two page sizes place
  black-bordered pages by their art, the pages around them and their borders.
  A short summary says how many screenshots have pages of each size.
- **Page sizes in crop review.** Choose a page size for one screenshot with
  radio buttons, or for the whole book at once, or measure one screenshot again
  on its own.
- **Overlapping halves.** Spreads sliced with a repeated strip of art at the
  seam are found and joined lined up, and the Seam tools line up any spread by
  hand (I and O move the halves, Enter shows the seam line).
- **Page folders in any format.** With no archive, pages are saved as JPEG,
  WebP or PNG; lossless PNG folders can be added to any export.
- **A lossless WebP suggestion** when it would make a comic much smaller, as
  with clean black-and-white line art.
- **Skip pair review** for comics whose pages are already complete, such as a
  CBR to convert to CBZ.
- **Truer colour** for CMYK pages without a colour profile, converted as printed
  for North American comics (SWOP).
- **RAR5 comics on Linux**, and on Windows, uninstalling can remove your settings
  too.

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
