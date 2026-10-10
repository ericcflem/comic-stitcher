@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "compile_runtime_installer_windows.ps1" -Compiler "%~1" > "runtime-installer-retry.log" 2>&1
if errorlevel 1 goto :failed
type "runtime-installer-retry.log"
echo.
echo PASS: installer recompiled in installer_dist. The existing app build was reused.
pause
exit /b 0
:failed
type "runtime-installer-retry.log"
echo.
echo Installer compilation failed; keep runtime-installer-retry.log.
pause
exit /b 1
