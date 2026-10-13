# Comic Stitcher 0.7.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **Linux.** Install from the source download with `linux/install.sh`. It sets up
  Comic Stitcher in your home folder with an app-menu entry, Open With for comic
  files and Open in Comic Stitcher for folders, and checks for the system
  libraries it needs. After a system upgrade to a new Python version, it offers
  to repair itself. See *Installing on Linux* in the README.
- **Drop tab.** A small tab on the right edge of the screen: drag comics onto it
  to open them without rearranging windows. On Windows the installer's option is
  ticked by default; on Linux the installer turns it on.
- **Covers and spreads in ComicInfo.xml.** When ComicInfo.xml is included, its
  page list now marks the front cover and two-page spreads for library apps.
- **PDF import resolution.** For PDFs made of page images, the rendering
  resolution is set to match the images, so you no longer need to guess.
- **Size estimate.** The export screen shows about how large the finished comic
  will be, updating as you change settings.
- **Output options by copy.** One tab for the full-size copy and one for the
  reduced copy, each with its own format settings.
- **Fixes.** Acrobat-made PDFs are offered original JPEG extraction again, and
  the smaller windows can no longer be squeezed until their contents overlap.

The full list is in [CHANGELOG.md](CHANGELOG.md).

The Windows x64 installer includes the required application dependencies; a
separate Python installation is not needed. Setup offers the Microsoft Visual C++
x64 prerequisite if required, under Microsoft's component terms. The installer's
third-party components are unchanged from 0.6.38.

## Limitations

- Page-pair suggestions require human review. Missed pairs can be joined manually.
- Western and Manga presets do not guarantee page order; check the final review.
- Saved page-review sessions do not resume interrupted exports or retain an entire
  queued batch after closing the application.
- WebP and CB7 support varies between comic readers.
- PDF import preserves embedded JPEG pages when they are compatible; other PDFs
  are rendered. PDF export is not included.
- Windows has an installer; Linux installs from source. There is no Mac or
  portable download. Comic Stitcher is not a comic reader or library manager.

See [README.md](../README.md) for instructions and
[SOURCE_ACCESS.md](SOURCE_ACCESS.md) for matching source, licenses and library
replacement information.
