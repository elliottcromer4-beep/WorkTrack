@echo off
rem Start WorkTrack without leaving a console window behind.
cd /d "%~dp0"
start "" pythonw main.py %*
