# Comic Stitcher 0.14.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **Linux AppImage.** One file that runs on most Linux desktops without
  installing anything, with UnRAR and `cwebp` built in. It offers to add itself
  to your app menu, with Open With and the Files folder menu, and Settings can
  take it all out again. It's new, so please report anything that doesn't work;
  `linux/install.sh` is still there too.
- **Scroll bars you can see,** in light and dark mode.
- **Linux follows the desktop's dark mode** more reliably, with clearer check
  boxes in light mode.

The full list is in [CHANGELOG.md](CHANGELOG.md).

The Windows x64 installer includes the required application dependencies; a
separate Python installation is not needed. Setup offers the Microsoft Visual C++
x64 prerequisite if required, under Microsoft's component terms. The installer's
Python libraries are unchanged from 0.6.38, and it includes Google's cwebp
encoder (libwebp 1.6.0, unchanged from 0.8.0) for WebP's sharp color conversion.

The Linux AppImage (x86-64) carries the same Python library versions, as Linux
builds, with UnRAR 7.20 and cwebp 1.6.0. It needs 64-bit Linux from about 2022
on (Ubuntu 22.04, Debian 12, Fedora 36 or newer).

## Limitations

- Page-pair suggestions require human review. Missed pairs can be joined manually.
- Western and Manga presets do not guarantee page order; check the final review.
- Saved page-review sessions do not resume interrupted exports or retain an entire
  queued batch after closing the application.
- WebP and CB7 support varies between comic readers.
- PDF import preserves embedded JPEG pages when they are compatible; other PDFs
  are rendered. PDF export holds JPEG and PNG pages, not WebP, and has no
  ComicInfo.xml.
- Windows has an installer; Linux has the AppImage (x86-64 only) or installs
  from source. There is no Mac download. Comic Stitcher is not a comic reader or library manager.

See the [User Guide](USER_GUIDE.md) for instructions and
[SOURCE_ACCESS.md](SOURCE_ACCESS.md) for matching source, licenses and library
replacement information.
