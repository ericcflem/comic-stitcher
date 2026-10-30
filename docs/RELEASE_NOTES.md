# Comic Stitcher 0.13.0

Comic Stitcher prepares comic archives. Import PDFs, comic archives, image folders
or centered screenshots; review suggested page pairs; join split spreads; and
check the finished comic before exporting. Original input files are preserved.

## What's new

- **Dark mode.** When Windows or your Linux desktop is set to dark, so is Comic
  Stitcher: dark panels, brighter text, light icons, and check boxes you can
  tell apart.
- **Help, offline.** A **?** button beside the settings gear, **F1** on any
  screen, opens the User Guide in its own window, with contents links and
  Find; **?** shows the keyboard shortcuts.
- **Problems in plain words.** A full drive, a folder that can't be changed, a
  file open in another program, a OneDrive file that's only online, or an
  unreachable network drive is explained, with what to do.
- **Disk space is checked first,** before importing, exporting or running the
  queue, with a warning if the drive looks too full.
- **Saving somewhere else** when the original's folder can't be written to (a
  disc, say): Comic Stitcher asks for another folder instead of failing.
- **Long paths on Windows:** the installer offers to allow paths over 260
  characters, and so does the message if one is too long.

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
