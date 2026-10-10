# Source package contents

| Location | Contents |
| --- | --- |
| `comic_builder/`, root Python files | Application code, tests, packaging helpers and verification tools |
| `README.md`, `LICENSE`, `docs/` | User guide, Windows build/replacement instructions, component scope and source access |
| `assets/` | App/page/interface icons and retained component notices |
| `third_party_sources/` | Ten complete version-specific library source archives |
| `third_party_notices/` | Notice texts extracted from all 26 exact pinned build-input wheels, plus the matching CPython notice; a conservative superset of final runtime notices |
| `vendor/` | Verified official UnRAR executable, original licence and provenance |
| `installer/`, `build_hooks/` | Installer source, Microsoft terms/input bindings and Qt packaging hook |
| `audit/qt-supplier-configuration/` | Supplier-generated Qt feature headers and matching-section provenance; technical build information, not private user logs |
| `audit/microsoft-runtime/bootstrapper-findings.json` | Hash-bound supplier prerequisite evidence required by the terms verifier |

`SOURCE_PACKAGE_FILES.json` lists every packaged file except itself, with size
and SHA-256. `retained-source-manifest.json` identifies the ten source archives;
`third_party_notices/NOTICE_INPUTS.json` identifies notice supplier inputs.

This package contains no compiled Comic Stitcher app/installer, virtual environment,
wheelhouse, Microsoft installer EXE, comics, user settings, API keys or private
Windows/VM test logs. Build scripts download hash-pinned wheels and Microsoft's
verified prerequisite. The separately licensed UnRAR executable is intentionally
included as a build input.

A GitHub automatically generated source snapshot will not include ignored source
archives or vendor binaries if those were omitted from the repository. The full
versioned source ZIP is the corresponding-source download; publish that complete
asset alongside the matching installer and link it explicitly.
