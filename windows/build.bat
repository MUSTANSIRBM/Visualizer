@echo off
echo Activating virtual environment...
call .venv\Scripts\activate.bat
echo.
echo Building MyVisualizer.exe...
python -m PyInstaller --onedir --windowed --name "MyVisualizer" --collect-all pyaudiowpatch --add-data "colors.txt;." --add-data "icon.png;." --icon "icon.ico" visualizer.py
echo.
echo Build complete! Check the 'dist\MyVisualizer' folder.
pause