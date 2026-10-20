# Native-library source and rebuild guide — 0.8.0 (library sources unchanged since 0.6.38)

Prepared 9 October 2026 UTC. This guide separates a normal application rebuild,
rebuilding upstream libraries, and evidence about the supplier's binaries.
Windows native-source commands below are a reviewed recipe, **not an executed
Windows native-source build**. Do not describe them as Windows-tested yet.

## What is already demonstrated

The pinned application rebuild, modified py7zr frozen-module test and coherent
prebuilt Qt/PySide replacement have passed on Windows. A replacement library must
keep the interfaces and architecture the application expects. These checks do
not promise that arbitrary library modifications will work. Users may modify the
LGPL libraries and reverse engineer the application to debug those modifications.

The replacement method does not require the distributor to maintain arbitrary
third-party modifications. Its purpose is to give users a usable way to rebuild
or replace the libraries; any incompatible changes remain the modifier's task.

## Retained sources and their build systems

| Shipped component | Matching retained source | Build route |
| --- | --- | --- |
| Qt Core, Gui, Widgets, Network, OpenGL; base plugins/translations | qtbase 6.11.2 | configure.bat, CMake, Ninja, MSVC |
| Qt Svg; SVG icon/image plugins | qtsvg 6.11.2 | installed Qt's qt-configure-module.bat, CMake |
| Additional image plugins including TIFF/WebP | qtimageformats 6.11.2 | installed Qt's qt-configure-module.bat, CMake |
| PySide6 and Shiboken6 runtime/bindings/generator sources | pyside-setup 6.11.2 | setup.py, create_wheels.py, Qt, libclang |
| py7zr 1.1.3 | py7zr-1.1.3.tar.gz | setuptools, setuptools_scm; pure Python |
| multivolumefile 0.2.3 | multivolumefile-0.2.3.tar.gz | setuptools, wheel, setuptools_scm; pure Python |
| pybcj 1.0.8 | pybcj-1.0.8.tar.gz | setuptools C extension, MSVC |
| pyppmd 1.3.1 | pyppmd-1.3.1.tar.gz | setuptools C extension, MSVC |
| inflate64 1.0.4 | inflate64-1.0.4.tar.gz | setuptools C extension, MSVC |
| setuptools' vendored MPL validation sources | setuptools-84.0.0.tar.gz | setuptools' self-contained backend; retain vendored notices |

All ten complete archives are in third_party_sources/. Their versions, source
locations, sizes and SHA-256 values are recorded in retained-source-manifest.json.
The source-verification report also records each archive's actual build entry
points and declared build requirements. Run from the application source root:

```bat
py -3.14 verify_native_sources.py --output native-source-check.json
py -3.14 verify_native_sources.py --extract-to C:\cs-native\src
```

The extraction destination must not already exist. This checks hashes before
extraction and refuses links, special files and unsafe archive paths. It does not
execute any upstream code. Extraction was exercised on Linux; native compilation
and Windows tool discovery were not.

## Build the archive-library sources

Use a separate source-building environment, not the application's .venv-release.
For the three C extensions, open **x64 Native Tools Command Prompt for VS 2022**.
Install Desktop development with C++ and an x64 Windows SDK through Visual Studio
Installer if those components are absent. Having the Visual Studio application
installed alone does not establish that the C++ toolchain is present.

```bat
py -3.14 -m venv C:\cs-native\archive-env
C:\cs-native\archive-env\Scripts\python.exe -m pip wheel --no-deps --wheel-dir C:\cs-native\wheels C:\cs-native\src\pybcj-1.0.8
```

Repeat the second command with pyppmd-1.3.1, inflate64-1.0.4, py7zr-1.1.3,
multivolumefile-0.2.3 or setuptools-84.0.0 as required. pip's isolated build loads
each source archive's own backend requirements. These are separate from the
application's pinned runtime wheel list. Save the complete build log, exact
resolved build-tool versions, any source edits and produced wheel hashes. For
offline reproducibility, retain the resolved source-build dependency wheels too.
Do not substitute a newly resolved dependency set for the published reference.

For each package, explicitly force-install its particular produced wheel into an
isolated application .venv-release, using --no-deps --no-index. Do not use a broad
wildcard which could select unrelated wheels. Follow the frozen-module rebuild
and marker test in REBUILDING_AND_REPLACING_LIBRARIES.md for Python libraries.
Use the same CPython 3.14 x64 ABI for non-abi3 native extensions. A Linux wheel is
not a Windows replacement even when the source version is identical.

## Build a coherent Qt/PySide replacement from source

This is a compatible replacement recipe, not a reconstruction of every supplier
configure flag. Retain the supplier source archives unchanged and work on separate
extracted copies. Keep paths short and free of spaces. The versioned Qt 6.11
documentation specifies MSVC 2022, CMake 3.22 or newer, Ninja and Python, with
Windows SDK 10.0.17763 or newer. Record the exact versions actually selected.

From the x64 Native Tools command prompt, with cmake, ninja and python available:

```bat
mkdir C:\cs-native\qtbase-build
cd /d C:\cs-native\qtbase-build
C:\cs-native\src\qtbase-everywhere-src-6.11.2\configure.bat -opensource -confirm-license -release -shared -nomake examples -nomake tests -prefix C:\cs-native\qt-install
cmake --build . --parallel 6
cmake --install .
```

Use the installed Qt to configure each additional module in its own build folder:

```bat
mkdir C:\cs-native\qtsvg-build
cd /d C:\cs-native\qtsvg-build
C:\cs-native\qt-install\bin\qt-configure-module.bat C:\cs-native\src\qtsvg-everywhere-src-6.11.2
cmake --build . --parallel 6
cmake --install .
```

Repeat for qtimageformats in a qtimageformats-build folder. Preserve CMakeCache.txt,
config.summary/config.opt when generated, configure/build logs, compiler/SDK
versions, selected optional libraries and their versions, and all source patches.
Optional feature detection can change the result between machines; the commands
alone do not pin every feature. Never mix private-ABI-sensitive Qt/PySide binaries
from incompatible builds.

For PySide, use a separate CPython 3.14.2 x64 environment and the extracted
pyside-setup-everywhere-src-6.11.2 directory. Install its own requirements.txt;
it pins several build tools independently of our application environment.
Shiboken needs libclang with LLVM's CMake configuration files. The versioned Qt
guide identifies Qt's libclang 20.1.3-based x64 package as a suitable example;
the generic Windows LLVM installer is not necessarily sufficient. Record the
download, version and hash actually used, and set LLVM_INSTALL_DIR to its root.

```bat
python setup.py build --qtpaths=C:\cs-native\qt-install\bin\qtpaths.exe --standalone --limited-api=yes --module-subset=Core,Gui,Widgets,Network,Svg --parallel=6
python create_wheels.py --build-dir=C:\cs-native\src\pyside-setup-everywhere-src-6.11.2\YOUR_ACTUAL_BUILD_DIRECTORY --no-examples
```

Replace YOUR_ACTUAL_BUILD_DIRECTORY with the generated directory containing
**package_for_wheels**. That spelling was checked in the retained 6.11.2 source.
The command builds only the bindings this application imports; it is not a promise
that the minimal wheels are byte-identical to the supplier's full wheels. The
retained create_wheels.py makes wheels under dist/. Install a coherent matching
PySide6, PySide6_Essentials, PySide6_Addons and shiboken6 set into the application
build environment, using explicit filenames. shiboken6_generator is a build tool,
not an additional application runtime requirement.

Build the app with rebuild_release_windows.bat rather than rerunning the original
hash-selected preparation step. Observe loaded DLL paths and hashes as in the
completed native replacement test, and test widgets, SVG icons and ordinary image
previews. Preserve the new library builds and their configuration records. A new
dependency version or optional codec requires its own notices/inventory review.

## Other native binaries and notices

Pillow's combined supplier notice covers its codecs, NumPy's combined notice
covers its numerical dependencies, and the PDFium wheel includes its component
notices. CPython's LICENSE covers its incorporated components; OpenSSL 3.0.18 has
separate retained Apache notice material. These licenses do not all impose the
same source-publication duties as LGPL. Retaining permissive notices is required;
copying every permissively licensed upstream repository is not our blanket plan.

Qt's opengl32sw.dll is a separate Mesa/LLVM software renderer. Its bytes in the
pinned Essentials wheel contain Mesa 11.2.2 and LLVM 3.6.2 version strings. Notice
material from those source releases is retained and hash-bound to that DLL.
The provenance report preserves the distinction between version evidence and
unknown supplier configure options. Historical Mesa docs/COPYING contains a
generic LGPL text; its presence is not evidence that all code in this renderer is
LGPL. The version-specific licence page and file notices control their components.

Microsoft's runtime remains a separate supplier prerequisite. UnRAR retains its
own extraction-only supplier redistribution terms. Neither is relicensed under
Comic Stitcher's MIT licence.

## Publication and remaining verification

Use direct versioned source downloads alongside the installer, rather than a
promise to supply sources later. Include the complete selected source archives,
application/build scripts, changes, notices and replacement instructions. Public
addresses must be filled in only after the actual repository/release exists.

The effective Qt feature configuration is retained under
audit/qt-supplier-configuration/: five SDK DLLs have exactly matching raw PE
sections to the pinned wheel, and the SDK supplies generated feature headers and
qconfig.pri. This does not recover the full configure cache, every supplier patch
or PySide generator/toolchain details. The reviewed native recipe also still needs Windows
execution if it is to be called tested. These are specific evidence gaps, not a
request to repeat installer, VM runtime or comic-processing tests indiscriminately.
Source compilation is optional for this release unless a specific missing source
or necessary build-material obligation is identified. 

Primary references, checked 9 October 2026 UTC:

- https://doc.qt.io/qt-6.11/windows-building.html
- https://doc.qt.io/qtforpython-6.11/building_from_source/windows.html
- https://doc.qt.io/qt-6.11/lgpl.html
- https://www.qt.io/development/open-source-lgpl-obligations
- https://www.mozilla.org/en-US/MPL/2.0/
- https://wiki.qt.io/MesaLlvmpipe
- Retained release archives and wheel-inputs.json, rather than moving upstream branches.
