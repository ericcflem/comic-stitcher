# Building Comic Stitcher on Windows

The reference build targets Windows x64 with **CPython 3.14.2 x64** and
**Inno Setup 7.1.0**. The application version is 0.7.0. These are the recorded
build inputs. On Linux, Comic Stitcher is installed from source with
`linux/install.sh` (see the README); there is no Linux or Mac build to make here.

## Fresh build

1. Install the specified Python and Inno Setup compiler.
2. Extract this source ZIP into a fresh folder. Keep `assets/`, `build_hooks/`,
   `installer/`, `vendor/` and the other supplied files together.
3. Open a terminal in that folder and run:

```bat
build_release_windows.bat --prepare-only
build_runtime_installer_windows.bat
```

Stop if either command reports failure. The first creates `.venv-release`,
downloads the exact hash-selected wheels into `release-wheelhouse/`, installs
them and checks dependency consistency. The second builds `dist/ComicStitcher/`,
collects notices, applies the recorded shared-runtime packaging changes, prepares
Microsoft's prerequisite and compiles the installer into `installer_dist/`.
Internet access is needed for the pinned supplier downloads.

CBR/RAR import uses RARLAB's official `unrar.exe`, placed at `vendor/unrar.exe`.
The release source package includes it, with its licence and provenance. The
GitHub repository, and GitHub's **Download ZIP**, contain only `vendor/license.txt`
and `vendor/UNRAR-PROVENANCE.json`, because the executable is separately licensed
freeware. When building from GitHub, download `unrarw64.exe` from the address in
`vendor/UNRAR-PROVENANCE.json`, run it to extract `unrar.exe`, and put that in
`vendor/`. The build checks its SHA-256 against the recorded version. RARLAB's link
always serves the newest version, so if it is newer than the recorded one, use the
recorded version's `unrar.exe` from a release source package, or update the record
and its hashes as a reviewed change.

No ImageMagick, Ghostscript, Poppler, ffmpeg or external 7-Zip installation is used.
The normal wheel-based build does not require compiling Qt or the archive codecs.

The Microsoft prerequisite is downloaded from a fixed reviewed URL. Its hash,
version, signature and agreement binding are checked. The build does not run
Microsoft's installer or accept its agreement on your behalf.

If compiler discovery fails, supply the actual ISCC.exe location:

```bat
build_runtime_installer_windows.bat "C:\path\to\Inno Setup 7\ISCC.exe"
```

## Repeat builds

Run `build_runtime_installer_windows.bat` again to incorporate source or document
changes. To compile Setup from an already rebuilt app folder, use
`retry_runtime_installer_windows.bat`; this does **not** rebuild the application
or refresh its bundled documents. Do not compile `setup_windows.iss` alone before
preparing its verified prerequisite and payload inputs.

The ordinary `build_windows.bat` uses broad development requirements and a
separate deployment route. Use the pinned commands above to reproduce this release.
Do not casually regenerate `requirements-release.txt` or `wheel-inputs.json`.

## Make the source package

Each release publishes a source package next to the installer. The library
source archives (`third_party_sources/*.tar.*`) and `vendor/unrar.exe` are not in
the GitHub repository; copy them from the previous release's source package
(they only change when the pinned dependencies do). Then run:

```bat
py -3.14 make_source_package.py
```

It checks every file against `SOURCE_PACKAGE_FILES.json` (size and SHA-256),
writes `dist\ComicStitcher_Source_<version>.zip`, and writes `dist\SHA256SUMS.txt`
with the checksums of the installer and the source package for the release page.
Build the installer first, so it is included. It stops and lists anything missing
or different.

Run it in an exact copy of the release's files: the release's **Source code
(zip)** from GitHub, or a git clone made with `core.autocrlf` set to `false`.
Git for Windows otherwise changes line endings, and every text file would then
differ from its recorded hash.

## Check a resulting installer

Install the newly built EXE, then run:

```bat
verify_runtime_install_windows.bat
```

For a non-default location, pass the installed application folder as a quoted
argument. The result is `runtime-integration/installed-payload-result.json`.
It compares the installed files with this build's recorded payload; a pass is
not a functional GUI test or a promise of byte-identical rebuilt executables.

For a functional check, import a small comic, review pages and export a copy.
Confirm that the copy opens in your comic reader and the originals are unchanged.
For changes to the application, check the affected workflows as well.

Retain this build's input payload manifest, installer build record, compiler
version and final checksums. Logs may contain personal paths; keep raw evidence
out of a public repository.

## Microsoft runtime installation

Setup checks for the required x64 system runtime. If it is missing, Setup offers
Microsoft's installer before deploying Comic Stitcher. Microsoft handles its
agreement and any administrator prompt. Cancellation, failure or a requested
restart stops Comic Stitcher installation; restart if needed and rerun Setup.
An existing suitable runtime skips prerequisite installation.

Comic Stitcher's Microsoft component terms page appears even when a suitable
runtime is already installed. Silent Setup requires `/ACCEPTMICROSOFTTERMS=yes`
after reading those terms, and requires a suitable runtime to be installed already.
It does not silently install Microsoft's prerequisite. Uninstalling Comic Stitcher
does not remove Microsoft's shared runtime.

The reference installer has been exercised with missing-runtime installation,
decline/cancellation, elevation denial, successful installation, upgrade and silent
refusal. Failure and restart responses were also checked using controlled return
codes; no actual Microsoft-triggered reboot is claimed. Changes to runtime handling
need checks of the affected paths. Redistribution details are in
[SOURCE_ACCESS.md](SOURCE_ACCESS.md); the original agreements and component terms
are retained with the source and installed notices.

## Build records and verification scope

Each build produces its own build-inventory.json, payload manifest and installer
record. The installed-file verifier compares that build's files and checks for
unexpected native libraries. A successful comparison verifies the installed payload;
it does not test all application behavior or establish complete license compliance.
Check the component and notice inventory against the resulting build. Keep compiler
versions, exact input records and output checksums with the release evidence.

## Development checks

Development dependencies are separate from the pinned release environment.
From the source root with those dependencies installed:

```bat
python -m ruff check .
python -m unittest discover -s comic_builder -t .
python -m unittest test_installed_runtime_verifier test_qt_pdf_exclusion comic_builder.test_license_packaging
```

Windows credential and local socket tests need the appropriate platform/access.
A skipped test is not a passed test. Native build recipes are in
[NATIVE_SOURCE_REBUILD.md](NATIVE_SOURCE_REBUILD.md).
