# Microsoft runtime deployment

This Windows build requires Microsoft Visual C++ v14 x64 runtime 14.44.35211.0
or later. The Comic Stitcher installer checks the five required System32 DLLs
and offers Microsoft's unmodified redistributable installer when necessary.
Microsoft's installer presents its own agreement and may request administrator
permission. Comic Stitcher's uninstaller never removes the shared runtime.

Two NumPy 2.5.3 extension modules have their imported DLL name changed from the
wheel's private MSVCP140 name to standard MSVCP140.dll. Their functions are
unchanged; PE checksums are regenerated. Exact original/modified hashes and the
removed runtime files are recorded in build-inventory.json. NumPy retains its
BSD license. Microsoft DLL code is not modified.

The source includes package_shared_runtime.py and installer/shared-runtime-inputs.json.
Rebuild with the pinned release requirements, then apply the recorded packaging
step. A dependency update must be audited before accepting new hashes.

This record describes deployment; it is not a declaration of complete release
licensing clearance. Publisher redistribution terms still apply to the bundled
Microsoft installer. Portable distributions are deferred.
