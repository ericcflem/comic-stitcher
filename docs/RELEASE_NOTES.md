# Comic Stitcher 0.6.38 — first public release

Comic Stitcher is a Windows application for preparing comic archives. Import PDFs,
comic archives, image folders or centered screenshots; review suggested page pairs;
join split spreads; and check the finished comic before exporting. Original input
files are preserved.

## Features

- Review suggested spreads side by side and inspect their seams in the same window.
- Reorder, rotate, join or split pages during final review.
- Convert pages to JPEG, PNG or WebP, with adjustable quality and processing workers.
- Export full-size and reduced copies as CBZ, CB7 or PNG page folders.
- Review multiple comics as a batch, check output names and settings, remove an
  item or add another comic before starting exports.
- Save page-review sessions and optionally edit ComicInfo.xml metadata or look up
  information through ComicVine using your own API key.

The Windows x64 installer includes the required application dependencies; a
separate Python installation is not needed. Setup offers the Microsoft Visual C++
x64 prerequisite if required, under Microsoft's component terms.

## Limitations

- Page-pair suggestions require human review. Missed pairs can be joined manually.
- Western and Manga presets do not guarantee page order; check the final review.
- Saved page-review sessions do not resume interrupted exports or retain an entire
  queued batch after closing the application.
- WebP and CB7 support varies between comic readers.
- PDF import preserves embedded JPEG pages when they are compatible; other PDFs
  are rendered. PDF export is not included.
- This release provides a Windows x64 installer. Mac, Linux and portable downloads
  are not included. Comic Stitcher is not a comic reader or library manager.

See [README.md](../README.md) for instructions and
[SOURCE_ACCESS.md](SOURCE_ACCESS.md) for matching source, licenses and library
replacement information.
