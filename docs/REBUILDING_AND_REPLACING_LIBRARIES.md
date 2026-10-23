# Comic Stitcher 0.9.0: reference rebuild and replacement recipe

The pinned application rebuild, modified py7zr rebuild and coherent official
prebuilt Qt/PySide replacement were exercised on Windows. Native Qt/PySide source
compilation commands are reviewed but have not been executed on Windows.
The source compilation recipes are provided with that validation limit.

## Reference build

1. Use 64-bit Windows and CPython **3.14.2 x64**, matching the audited build. The application's wheel-based build does not compile every dependency from source.
2. Extract this consolidated package into a fresh folder; no overlay is required. Keep its `assets` and `build_hooks` directories. If RAR support is required, retain `vendor/unrar.exe` and the matching licence from the verified official supplier package.
3. Run `build_release_windows.bat`. It creates a separate `.venv-release`, downloads hash-selected wheels into `release-wheelhouse`, installs them without an index, checks dependency consistency, and builds the app using the original packaging options. It deliberately does not run the ordinary build script, which upgrades and resolves broad requirements.
4. Keep `release-wheelhouse` with the release build inputs. Check the resulting application and notice inventory against the audited reference (its third-party components are unchanged since 0.6.38). Run the existing regression tests and Windows smoke tests. For an installer, use `build_runtime_installer_windows.bat` with the prepared pinned environment; it prepares the verified Microsoft prerequisite before compiling Setup. Retain compiler/version and payload records.

The requirements contain the original 26 installed package versions, including build tooling. Their hashes select official Windows-compatible wheels; they were resolved from official PyPI metadata during reference-input verification. A chosen wheel's compatibility and version do not prove every shipped binary is identical. All 26 selected wheels were downloaded and checked for exact hash, package identity and default dependency metadata consistency during reference-input verification, without installing or executing their code. Four supplier wheels also had their shipped native contents matched separately. The pinned recipe and installed-payload comparison were exercised successfully on Windows. Each rebuild requires its own verification. It does not promise byte-identical executables or installers: timestamps, compiler/tool versions, compression and other build details can differ.

`collect_release_inputs.py` records how the wheel list was selected. Do not rerun it casually for a published release; retain the committed inputs and hashes. Wheel files are downloaded by the pinned preparation step, not bundled here. Matching selected source archives are in `third_party_sources/`.

## Test a modified frozen LGPL Python library

This is a practical, harmless test of the rebuild route. It is not a requirement that arbitrary modifications work.

1. Use the isolated source copy. Run `build_release_windows.bat --prepare-only` to prepare the reference environment.
2. Extract the retained **py7zr 1.1.3** source archive into a separate directory. Keep its complete upstream licence and notices. Add this harmless constant at the end of `py7zr/__init__.py`:

   ```python
   COMIC_STITCHER_REBUILD_MARKER = 'modified-library-test-1'
   ```

3. In that source directory, build a wheel using a separate source-build environment and upstream build requirements. Record all those requirements, tools and their versions as part of the test. Example command after preparing that environment: `python -m pip wheel --no-deps --wheel-dir modified-wheels .`. Do not assume the reference runtime environment contains every source-build tool.
4. Install that specific modified wheel into `.venv-release` with `--force-reinstall --no-deps --no-index`. Use its exact local filename, not a wildcard. This intentionally replaces the reference hash-selected library; retain the new wheel's hash and the patch. Do not rerun `build_release_windows.bat` afterward, as pip may treat a same-version modified package as already satisfied. Restore it by explicitly force-reinstalling the verified original wheel with --no-deps --no-index, or use a new environment.
5. From the application source root, run `rebuild_release_windows.bat --probe`. This uses a temporary observer entry point which runs the normal GUI unless explicitly asked to verify the library. It also retains normal notice collection.
6. Run the generated executable with `--verify-library` and an absolute writable JSON output filename. In PowerShell, use `Start-Process -Wait -PassThru` to wait for this windowed executable. For example, from the source root:

   ```powershell
   $resultPath = Join-Path (Get-Location) 'modified-library-result.json'
   $process = Start-Process -FilePath '.\dist\ComicStitcher\ComicStitcher.exe' -ArgumentList @('--verify-library', ('"' + $resultPath + '"')) -Wait -PassThru
   $process.ExitCode
   Get-Content -LiteralPath $resultPath
   ```

   Require exit code 0, `frozen: true`, marker `modified-library-test-1`, and a module path belonging to the packaged app, not the development source folder. Run this from a fresh Windows account without developer PYTHONPATH settings as a further isolation check.
7. Launch the same executable normally and test an archive export. Preserve the JSON, build log, source patch, modified wheel and hashes. A successful observer check establishes that the frozen app uses the changed copy; the export check establishes that this harmless change did not break that workflow.
8. Do not distribute the probe build as the normal release. Restore the unmodified reference environment or create a clean isolated copy and build with the default `launch.py` entry point.

For other LGPL dependencies, use their matching source versions and documented build process; native extensions may require Visual C++ tooling. Do not replace a native module with a binary for a different Python ABI or architecture.

## Qt/PySide native replacement

The application is an onedir distribution with separate Qt/PySide/Shiboken binaries, not one sealed executable. Keep the entire app folder together. Users may modify these libraries under their applicable licences.

For a reliable route, build a compatible set from the matching Qt/PySide source releases, install that set in an isolated app build environment, and rebuild the application. Keep matching architecture, Python stable ABI support, and mutually compatible Qt/PySide/Shiboken versions. Include the used Qt modules and plugins; replace a coherent set rather than mixing an arbitrary DLL into the installed application. Retain generated build configurations and upstream dependencies needed to rebuild the modified libraries.

The retained PySide source CI/build scripts include `--standalone`, `--limited-api=yes`, Qt paths and Windows-specific options. They identify supplier inputs but are not a substitute for an executed Windows recipe. The official prebuilt native replacement test passed on Windows. Native source compilation is an optional further check; these are distinct checks.

Official build references:
- https://doc.qt.io/qtforpython-6/building_from_source/windows.html
- https://doc.qt.io/qt-6.11/windows-building.html

## Source delivery and licence records

This package retains complete upstream archives for Qt/PySide, five LGPL archive libraries and setuptools' MPL-covered validation sources. The archive manifests identify versions and hashes. This is source collection; it does not by itself establish that all generated configuration, dependencies, installation information or corresponding-source obligations have been satisfied.

When publishing binaries, publish the matching application sources, applicable complete library sources, notices and the tested rebuild/replacement instructions through the chosen compliant delivery arrangement. Keep these version-specific assets accessible while distributing the corresponding binaries. Do not replace them with moving upstream links or claim an unimplemented written source offer.

## PDF dependency packaging

The integrated QtGui hook excludes qpdf before dependency collection. Run
`python verify_no_qt_pdf.py dist/ComicStitcher` after building. PDF rendering uses pypdfium2/PDFium and embedded JPEG extraction uses pypdf.
QtPDF and QtWebEngine are not application dependencies. The check guards against
collecting unused Qt PDF libraries into the Windows bundle.

## Detailed native-source guide

See NATIVE_SOURCE_REBUILD.md for source-verified build entry points, required
toolchains and a reviewed Windows native-source recipe. Native source compilation has not been executed on Windows; the application
rebuild and compatible prebuilt-library replacement routes have been tested.
