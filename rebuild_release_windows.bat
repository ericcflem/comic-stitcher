@echo off
setlocal
cd /d "%~dp0"
set "STITCHER_PYTHON=.venv-release\Scripts\python.exe"
if not exist "%STITCHER_PYTHON%" (
    echo Run build_release_windows.bat --prepare-only first.
    exit /b 1
)
set "STITCHER_ENTRY=launch.py"
if /I "%~1"=="--probe" set "STITCHER_ENTRY=rebuild_probe.py"
"%STITCHER_PYTHON%" -m pip check
if errorlevel 1 goto :failed
"%STITCHER_PYTHON%" package_icons.py
if errorlevel 1 goto :failed
"%STITCHER_PYTHON%" -m PyInstaller --noconfirm --clean --windowed --onedir --additional-hooks-dir "build_hooks" --exclude-module PySide6.QtVirtualKeyboard --collect-all pypdfium2 --add-data "assets\licenses\Phosphor-LICENSE.txt;." --hidden-import PySide6.QtSvg --hidden-import launch --icon "assets\app\comic-stitcher.ico" --add-data "assets\app\comic-stitcher.png;." --name ComicStitcher "%STITCHER_ENTRY%"
if errorlevel 1 goto :failed
"%STITCHER_PYTHON%" package_licenses.py
if errorlevel 1 goto :failed
if exist "vendor\unrar.exe" (
    if not exist "vendor\license.txt" goto :failed
    "%STITCHER_PYTHON%" package_unrar.py "vendor\unrar.exe" "vendor\license.txt" "dist\ComicStitcher"
    if errorlevel 1 goto :failed
)
if exist "vendor\cwebp.exe" (
    "%STITCHER_PYTHON%" package_cwebp.py "vendor\cwebp.exe" "dist\ComicStitcher"
    if errorlevel 1 goto :failed
)
"%STITCHER_PYTHON%" verify_no_qt_pdf.py dist\ComicStitcher
if errorlevel 1 goto :failed
"%STITCHER_PYTHON%" package_shared_runtime.py deploy dist\ComicStitcher
if errorlevel 1 goto :failed
echo Built dist\ComicStitcher. This does not assert an identical installer or completed release clearance.
exit /b 0
:failed
echo Release rebuild failed; keep the error output.
exit /b 1
