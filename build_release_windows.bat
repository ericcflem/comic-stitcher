@echo off
setlocal
cd /d "%~dp0"
if not exist "launch.py" (
    echo Copy this rebuild kit into an isolated copy of the 0.6.38 source folder first.
    exit /b 1
)
py -3.14 -c "import sys; assert sys.version_info[:3] == (3,14,2), 'Use Python 3.14.2 for this reference build'; assert sys.maxsize > 2**32, 'Use 64-bit Python'"
if errorlevel 1 goto :failed
py -3.14 -m venv .venv-release
if errorlevel 1 goto :failed
set "STITCHER_PYTHON=.venv-release\Scripts\python.exe"
if not exist "release-wheelhouse" mkdir "release-wheelhouse"
"%STITCHER_PYTHON%" -m pip download --require-hashes --only-binary=:all: --dest "release-wheelhouse" -r "requirements-release.txt"
if errorlevel 1 goto :failed
"%STITCHER_PYTHON%" -m pip install --no-index --find-links "release-wheelhouse" --require-hashes -r "requirements-release.txt"
if errorlevel 1 goto :failed
"%STITCHER_PYTHON%" -m pip check
if errorlevel 1 goto :failed
if /I "%~1"=="--prepare-only" exit /b 0
call rebuild_release_windows.bat
exit /b %errorlevel%
:failed
echo Release preparation failed; keep the error output.
exit /b 1
