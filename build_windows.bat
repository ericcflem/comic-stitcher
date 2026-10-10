@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto :failed
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :failed
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed
python package_icons.py
if errorlevel 1 goto :failed
python -m PyInstaller --noconfirm --clean --windowed --onedir --additional-hooks-dir "build_hooks" --exclude-module PySide6.QtVirtualKeyboard --collect-all pypdfium2 --add-data "assets\licenses\Phosphor-LICENSE.txt;." --hidden-import PySide6.QtSvg --icon "assets\app\comic-stitcher.ico" --add-data "assets\app\comic-stitcher.png;." --name ComicStitcher launch.py
if errorlevel 1 goto :failed
python package_licenses.py
if errorlevel 1 goto :failed
if exist "vendor\unrar.exe" (
    if not exist "vendor\license.txt" (
        echo Missing vendor\license.txt for vendor\unrar.exe.
        goto :failed
    )
    python package_unrar.py "vendor\unrar.exe" "vendor\license.txt" "dist\ComicStitcher"
    if errorlevel 1 goto :failed
) else (
    echo UnRAR not included: place the matching executable and license in vendor to enable bundled RAR import.
)
echo.
echo Built: dist\ComicStitcher\ComicStitcher.exe
echo Keep the complete ComicStitcher folder together.
if /I not "%~1"=="--no-pause" pause
exit /b 0
:failed
echo.
echo Build failed. Copy the error shown above when asking for help.
if /I not "%~1"=="--no-pause" pause
exit /b 1
