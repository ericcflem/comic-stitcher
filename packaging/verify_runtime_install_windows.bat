@echo off
setlocal
rem Run from the source folder, one level up, where the build's outputs go.
cd /d "%~dp0.."
set "STITCHER_INSTALLED=%~1"
if "%STITCHER_INSTALLED%"=="" set "STITCHER_INSTALLED=%LOCALAPPDATA%\Programs\Comic Stitcher"
.venv-release\Scripts\python.exe packaging\verify_installed_runtime.py "%STITCHER_INSTALLED%"
if errorlevel 1 goto :failed
echo.
pause
exit /b 0
:failed
echo.
echo Verification failed; keep the displayed error and runtime-integration report.
pause
exit /b 1
