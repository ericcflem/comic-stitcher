; Compile with build_installer_windows.bat on Windows after supplying official UnRAR.
#define ComicStitcherVersion "0.6.38"
#if !FileExists("vendor/microsoft/runtime-verified.iss")
  #error Run prepare_microsoft_runtime_windows.ps1 before compiling.
#endif
#include "vendor/microsoft/runtime-verified.iss"
#if GetSHA256OfFile("vendor/microsoft/vc_redist.x64.exe") != MicrosoftRuntimeSHA256
  #error Microsoft runtime installer does not match its verified hash.
#endif
#if GetSHA256OfFile("installer/component-terms.txt") != MicrosoftComponentTermsSHA256
  #error Microsoft component terms do not match the reviewed record.
#endif
[Setup]
LicenseFile=installer\component-terms.txt
SetupIconFile=assets\app\comic-stitcher.ico
AppId=ComicSpreadBuilder
AppName=Comic Stitcher
AppVersion={#ComicStitcherVersion}
AppPublisher=Comic Stitcher
DefaultDirName={autopf}\Comic Stitcher
DefaultGroupName=Comic Stitcher
PrivilegesRequired=lowest
; Keep the existing per-user installer registry view for upgrades.
ArchitecturesAllowed=x64compatible
OutputDir=installer_dist
OutputBaseFilename=ComicStitcher-Setup-{#ComicStitcherVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ComicStitcher.exe
ChangesAssociations=yes

[InstallDelete]
; Remove only obsolete files shipped by earlier Comic Stitcher bundles.
Type: files; Name: "{app}\_internal\PySide6\plugins\imageformats\qpdf.dll"
Type: files; Name: "{app}\_internal\PySide6\Qt6Pdf.dll"

; Remove earlier app-local runtimes so Windows uses the shared installation.
Type: files; Name: "{app}\_internal\numpy.libs\msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"
Type: files; Name: "{app}\_internal\PySide6\MSVCP140.dll"
Type: files; Name: "{app}\_internal\PySide6\MSVCP140_1.dll"
Type: files; Name: "{app}\_internal\PySide6\MSVCP140_2.dll"
Type: files; Name: "{app}\_internal\PySide6\VCRUNTIME140.dll"
Type: files; Name: "{app}\_internal\PySide6\VCRUNTIME140_1.dll"
Type: files; Name: "{app}\_internal\shiboken6\MSVCP140.dll"
Type: files; Name: "{app}\_internal\shiboken6\VCRUNTIME140.dll"
Type: files; Name: "{app}\_internal\shiboken6\VCRUNTIME140_1.dll"
Type: files; Name: "{app}\_internal\VCRUNTIME140.dll"
Type: files; Name: "{app}\_internal\VCRUNTIME140_1.dll"

[Files]
Source: "vendor\microsoft\vc_redist.x64.exe"; Flags: dontcopy
Source: "dist\ComicStitcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Comic Stitcher"; Filename: "{app}\ComicStitcher.exe"
Name: "{autodesktop}\Comic Stitcher"; Filename: "{app}\ComicStitcher.exe"; Tasks: desktopicon
; Starts the drop tab, or shows it again if it was hidden.
Name: "{group}\Comic Stitcher Drop Tab"; Filename: "{app}\ComicStitcher.exe"; Parameters: "--drop-tab"; Comment: "Show the Comic Stitcher drop tab on the right edge of the screen"

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"

Name: explorer; Description: "Add Comic Stitcher to Explorer Open with"; GroupDescription: "Explorer integration:"

Name: foldermenu; Description: "Add Open in Comic Stitcher to folder menus (single comics and batches)"; GroupDescription: "Explorer integration:"

Name: droptab; Description: "Show a drop tab on the right edge of the screen and start it when I sign in"; GroupDescription: "Drop tab:"; Flags: unchecked

[Run]
; The tab records its own sign-in entry, so turning it off from its menu is respected.
Filename: "{app}\ComicStitcher.exe"; Parameters: "--drop-tab --enable-autostart"; Flags: nowait; Tasks: droptab
Filename: "{app}\ComicStitcher.exe"; Description: "Launch Comic Stitcher"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Close a running drop tab and remove its sign-in entry before files are removed.
Filename: "{app}\ComicStitcher.exe"; Parameters: "--uninstall-drop-tab"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveDropTab"

[Registry]
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.cbz\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.cbr\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.cb7\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.zip\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.rar\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.7z\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ComicStitcher"; ValueType: none; Flags: deletekey
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "Comic Stitcher"; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ComicStitcher.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\ComicStitcher.Comic"; ValueType: string; ValueName: ""; ValueData: "Comic Stitcher input"; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\ComicStitcher.Comic\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ComicStitcher.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".cbz"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.cbz\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".cbr"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.cbr\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".cb7"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.cb7\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".zip"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.zip\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".rar"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.rar\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Applications\ComicStitcher.exe\SupportedTypes"; ValueType: string; ValueName: ".7z"; ValueData: ""; Flags: uninsdeletekey; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\.7z\OpenWithProgids"; ValueType: none; ValueName: "ComicStitcher.Comic"; ValueData: ""; Flags: uninsdeletevalue; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ComicStitcher"; ValueType: string; ValueName: ""; ValueData: "Open in Comic Stitcher"; Flags: uninsdeletekey; Tasks: foldermenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ComicStitcher"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\ComicStitcher.exe"; Flags: uninsdeletekey; Tasks: foldermenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\ComicStitcher\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ComicStitcher.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: foldermenu

#include "installer/microsoft_runtime.iss"
#include "installer/microsoft_terms.iss"
