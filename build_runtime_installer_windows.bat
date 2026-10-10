@echo off
setlocal
cd /d "%~dp0"
echo Step 1 of 2: Rebuilding Comic Stitcher and checking its files.
echo This can take several minutes. Progress is recorded in runtime-installer-build.log.
echo Please leave this window open until the build finishes.
echo.
call rebuild_release_windows.bat > "runtime-installer-build.log" 2>&1
if errorlevel 1 goto :failed
echo Step 2 of 2: Preparing the Microsoft runtime and compiling the installer.
echo Downloads and installer compression can take a few minutes.
echo Progress is still recorded in runtime-installer-build.log.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "compile_runtime_installer_windows.ps1" -Compiler "%~1" >> "runtime-installer-build.log" 2>&1
if errorlevel 1 goto :failed
type "runtime-installer-build.log"
echo.
echo PASS: runtime-integrated installer built in installer_dist.
pause
exit /b 0
:failed
type "runtime-installer-build.log"
echo.
echo Build failed; keep runtime-installer-build.log.
pause
exit /b 1
