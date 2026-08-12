@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   WorkTrack - install dependencies
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found on the PATH.
    echo Install Python 3.9 or newer from python.org, ticking
    echo "Add python.exe to PATH", then run this again.
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: installing the dependencies failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done.  Start WorkTrack with run.bat, or:  python main.py
echo ============================================================
pause
