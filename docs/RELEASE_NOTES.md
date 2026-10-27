# Comic Stitcher 0.11.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **A User Guide** explains each step and setting, with every keyboard shortcut.
  It's installed with the app, and the README is shorter.
- **Pages worth a look** are pointed out after import: gaps in the file
  numbering, where a page may be missing, and names that differ only in
  capitalisation. They're marked with a pink ! in the final review; **M** goes
  from one to the next.
- **Save the cover and ComicInfo.xml beside the comic,** as well as inside it.
- **More lossless WebP suggestions:** for PNG pages and folders of pages too,
  and fixed so that changing the format or quality no longer hides it.
- **Easier dragging:** a page dragged to either end of the final review's strip
  keeps scrolling, and the queue shows the pink drop line everywhere.
- **Tighter windows,** and fixes for a resumed review's blank final review and
  for buttons left over from a finished export.
- **For builders:** the build tools are in `packaging/`, and the code's folder
  is `comic_stitcher`.

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
