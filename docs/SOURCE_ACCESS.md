# Sources, licences and library replacement — 0.7.0

Repository: [ericcflem/comic-stitcher](https://github.com/ericcflem/comic-stitcher)

Release/download location: [Version 0.7.0](https://github.com/ericcflem/comic-stitcher/releases/tag/v0.7.0)

Matching source asset: [ComicStitcher_Source_0.7.0.zip](https://github.com/ericcflem/comic-stitcher/releases/download/v0.7.0/ComicStitcher_Source_0.7.0.zip)

The release page will provide the Windows installer and its matching source
package as separate downloads, with SHA-256 checksums. The source package includes
Comic Stitcher's code and build scripts, selected complete third-party sources,
notices and library replacement instructions. Keep that matching source asset
available for the corresponding binary release.

## What is supplied

| Component | Source supplied in this package |
| --- | --- |
| Comic Stitcher | Application, documentation, artwork and build helpers at the source root and in `comic_builder/` |
| Qt base modules/plugins | `third_party_sources/qtbase-everywhere-src-6.11.2.tar.xz` |
| Qt SVG modules/plugins | `third_party_sources/qtsvg-everywhere-src-6.11.2.tar.xz` |
| Qt additional image plugins | `third_party_sources/qtimageformats-everywhere-src-6.11.2.tar.xz` |
| PySide6 and Shiboken6 | `third_party_sources/pyside-setup-everywhere-src-6.11.2.tar.xz` |
| py7zr, pybcj, pyppmd, inflate64 and multivolumefile | Their version-specific archives in `third_party_sources/` |
| setuptools vendored MPL validation material | Complete `third_party_sources/setuptools-84.0.0.tar.gz` |

The ten archives are listed with versions and hashes in
`retained-source-manifest.json`. They are complete upstream releases, including
code the application does not use. They remain under their upstream licences.
Permissively licensed dependencies do not all require source duplication; their
supplier notices are retained in `third_party_notices/` and `assets/licenses/`.
The installed app's `licenses/` index records notices for its actual build.

Qt/PySide/Shiboken are used under the LGPLv3 route. The archive-processing stack
contains LGPL components. Users may modify those libraries and reverse engineer
the application to debug library modifications as permitted by their licences.
See [REBUILDING_AND_REPLACING_LIBRARIES.md](REBUILDING_AND_REPLACING_LIBRARIES.md)
for frozen Python library rebuilds and coherent native library replacement.
A changed library must retain compatible interfaces; no promise is made to
support arbitrary incompatible modifications.

Microsoft's runtime and UnRAR retain separate supplier terms. They are not
relicensed under MIT. Their required notices and Microsoft component conditions
are retained with this package and installed notices.

Comic Stitcher's own MIT grant is in [LICENSE](../LICENSE); its scope and AI-assisted
history are explained in [PROVENANCE.md](PROVENANCE.md).

## Component notices and build records

The source notice reference contains texts from the 26 pinned build-input wheels
and matching Python, plus native and icon notices. This is a conservative set:
including a notice does not mean that all of that component's code is shipped.
The Windows build collector records its actual environment and frozen modules,
creates the offline notice index, and records component ownership and native hashes.

Pillow and its image codecs, NumPy and its numerical components, PDFium/pypdfium2,
pypdf, Python, OpenSSL and other permissive dependencies retain their supplier
notices. Qt's separate Mesa/LLVM software renderer has version-specific notices
bound to the reviewed DLL hash. See [THIRD_PARTY_ACKNOWLEDGEMENTS.md](THIRD_PARTY_ACKNOWLEDGEMENTS.md)
for attribution and [BUILDING.md](BUILDING.md) for build verification.

The supplier license texts control their respective components. This guide
summarizes the packaging arrangement and does not replace those texts.

## Microsoft prerequisite redistribution

The Windows installer includes the unmodified Visual C++ v14 x64 Redistributable
14.51.36247.0. The source ZIP does not contain that EXE: the builder downloads the
fixed supplier URL and verifies its hash, version, Microsoft signature and agreement
binding. A different runtime needs a new review of its applicable terms.

`installer/microsoft-terms-review.json` identifies the executable, its agreement
link, the official DOCX, the paragraph transcription and retained bootstrapper
evidence. Microsoft's executable links to https://aka.ms/VCRedistLicense rather
than embedding the agreement text. The original English DOCX and transcription
are retained in `assets/licenses/microsoft/` and in the installed notice index.

The Community 2022 agreement and REDIST list provide the selected publisher route.
The runtime's installation/use agreement alone does not grant redistribution
permission. Redistributors must agree to and preserve `installer/component-terms.txt`,
pass its protective terms to distributors and external users, keep Microsoft's
package unmodified with an application providing significant primary functionality,
preserve notices and comply with the applicable publisher license. Do not distribute
the runtime alone, label it MIT-licensed or imply Microsoft endorsement. Independent
repackaging must establish its applicable redistribution grant.

These conditions apply only to Microsoft's component. Comic Stitcher's MIT license
and the other components' licenses, including LGPL replacement/debugging rights,
remain intact. The pass-through arrangement is implemented; it does not certify
legal compliance for every component. Preserve the supplier bytes, checks,
agreement binding and component terms when rebuilding. Installation and silent-mode
behavior are described in [BUILDING.md](BUILDING.md).
