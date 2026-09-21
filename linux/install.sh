#!/usr/bin/env bash
# Installs MyVisualizer to ~/.local/share and adds a desktop + launcher entry.
# Run build.sh first so dist/MyVisualizer exists.
set -e
cd "$(dirname "$0")"

APP_DIR="$HOME/.local/share/MyVisualizer"
BIN_DIR="$HOME/.local/bin"
APP_BIN="$BIN_DIR/myvisualizer"
APP_ICON="$HOME/.local/share/icons/hicolor/512x512/apps/myvisualizer.png"

if [ ! -f dist/MyVisualizer/MyVisualizer ]; then
    echo "dist/MyVisualizer not found. Run ./build.sh first."
    exit 1
fi

echo "Installing to $APP_DIR ..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR"
cp -r dist/MyVisualizer/. "$APP_DIR/"

cat > "$APP_BIN" <<'EOF'
#!/usr/bin/env bash
exec "$HOME/.local/share/MyVisualizer/MyVisualizer" "$@"
EOF
chmod +x "$APP_BIN"

mkdir -p "$HOME/.local/share/icons/hicolor/512x512/apps"
cp icon.png "$APP_ICON"

DESKTOP="$HOME/.local/share/applications/myvisualizer.desktop"
mkdir -p "$HOME/.local/share/applications"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=MyVisualizer
Comment=Real-time audio spectrum visualizer
Exec=$APP_BIN
Icon=myvisualizer
Terminal=false
Categories=AudioVideo;Audio;
EOF

echo "Installed. Launch with '$APP_BIN' (or look for MyVisualizer in your app menu)."