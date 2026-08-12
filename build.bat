@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   WorkTrack - build standalone executable
echo ============================================================
echo.

python -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo ERROR: could not install PyInstaller.
    pause
    exit /b 1
)

echo Running the test suite first...
python -m pytest -q
if errorlevel 1 (
    echo.
    echo ERROR: tests failed - not building.
    pause
    exit /b 1
)

echo.
echo Building...
python -m PyInstaller --noconfirm --clean WorkTrack.spec
if errorlevel 1 (
    echo.
    echo ERROR: build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done.  Executable: dist\WorkTrack.exe
echo ============================================================
pause
