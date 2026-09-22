@echo off
echo Activating virtual environment...
call .venv\Scripts\activate.bat
echo.
echo Running border-pixel bounds guard test...
python test_bounds.py
if errorlevel 1 (
    echo.
    echo BOUNDS TEST FAILED - aborting build.
    pause
    exit /b 1
)
echo.
echo Building MyVisualizer.exe (GUI window)...
python -m PyInstaller --onedir --windowed --name "MyVisualizer" --collect-all pyaudiowpatch --collect-all comtypes --collect-all pycaw --collect-all winsdk --add-data "colors.txt;." --add-data "icon.png;." --icon "icon.ico" visualizer.py
echo.
echo Build complete! Output: dist\MyVisualizer
pause