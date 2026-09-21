#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
LINUX_DIR="$(pwd)"

echo "Activating virtual environment..."
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi
.venv/bin/pip install pyinstaller

echo "Building MyVisualizer..."
.venv/bin/python -m PyInstaller \
    --onedir \
    --windowed \
    --name MyVisualizer \
    --collect-all sounddevice \
    --add-data "colors.txt:." \
    --add-data "icon.png:." \
    --icon icon.png \
    visualizer.py

echo "Build complete! Check the '$LINUX_DIR/dist/MyVisualizer' folder."