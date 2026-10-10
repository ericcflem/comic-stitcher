@echo off
setlocal
cd /d "%~dp0"
call rebuild_release_windows.bat
if errorlevel 1 goto :failed
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "compile_runtime_installer_windows.ps1" -Compiler "%~1"
if errorlevel 1 goto :failed
echo.
echo Installer built in installer_dist. Keep runtime-integration evidence.
pause
exit /b 0
:failed
echo.
echo Installer build failed. Copy the error above.
pause
exit /b 1
