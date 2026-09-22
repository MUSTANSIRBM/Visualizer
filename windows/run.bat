@echo off
rem MyVisualizer (Windows) - run launcher for CMD
rem Usage: run.bat [--verbose] [--bars N]
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
)

".venv\Scripts\python.exe" -c "import pygame, numpy, pyaudiowpatch" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" visualizer.py %*
endlocal