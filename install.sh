#!/bin/bash
set -e

REPO="https://raw.githubusercontent.com/ronanzcasey-dot/blockcast/main"
INSTALL_DIR="$HOME/blockcast"

echo ""
echo "┌─────────────────────────────┐"
echo "│         blockcast           │"
echo "│  screen → minecraft blocks  │"
echo "└─────────────────────────────┘"
echo ""

# ── Python check ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌  Python 3 not found. Install it from https://python.org and re-run."
    exit 1
fi

echo "✔  Python 3 found: $(python3 --version)"

# ── Install Python dependencies ───────────────────────────────────────────────
echo ""
echo "Installing Python dependencies..."
pip3 install -q mss Pillow numpy mcrcon pyobjc-framework-Quartz --break-system-packages 2>/dev/null \
    || pip3 install -q mss Pillow numpy mcrcon pyobjc-framework-Quartz
echo "✔  Dependencies installed"

# ── Download files ────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
curl -fsSL "$REPO/screen_capture.py" -o "$INSTALL_DIR/screen_capture.py"
curl -fsSL "$REPO/screen_display.sk" -o "$INSTALL_DIR/screen_display.sk"
echo "✔  Files downloaded to $INSTALL_DIR"

# ── Configure ─────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────"
echo "  Configuration"
echo "─────────────────────────────────"
echo ""

read -p "  Minecraft username: " MC_USER
read -p "  RCON password (from server.properties): " RCON_PASS
read -p "  RCON host (default: 127.0.0.1): " RCON_HOST
RCON_HOST="${RCON_HOST:-127.0.0.1}"
read -p "  RCON port (default: 25575): " RCON_PORT
RCON_PORT="${RCON_PORT:-25575}"

sed -i '' \
    -e "s/PLAYER        = \"YourUsername\"/PLAYER        = \"$MC_USER\"/" \
    -e "s/RCON_PASSWORD = \"yourpassword\"/RCON_PASSWORD = \"$RCON_PASS\"/" \
    -e "s/RCON_HOST     = \"127.0.0.1\"/RCON_HOST     = \"$RCON_HOST\"/" \
    -e "s/RCON_PORT     = 25575/RCON_PORT     = $RCON_PORT/" \
    "$INSTALL_DIR/screen_capture.py"

echo "✔  Config saved"

# ── Skript install ────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────"
echo "  Skript Setup"
echo "─────────────────────────────────"
echo ""

# Try to auto-detect server
DETECTED=""
for candidate in \
    "$HOME/minecraft-*/plugins/Skript/scripts" \
    "$HOME/server/plugins/Skript/scripts" \
    "$HOME/Desktop/server/plugins/Skript/scripts" \
    "$HOME/Documents/server/plugins/Skript/scripts"
do
    # expand glob manually
    for d in $candidate; do
        if [ -d "$d" ]; then
            DETECTED="$d"
            break 2
        fi
    done
done

if [ -n "$DETECTED" ]; then
    echo "  Found Skript scripts folder: $DETECTED"
    read -p "  Install screen_display.sk here? [Y/n]: " CONFIRM
    CONFIRM="${CONFIRM:-Y}"
    if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
        cp "$INSTALL_DIR/screen_display.sk" "$DETECTED/screen_display.sk"
        echo "✔  Skript installed"
        SK_INSTALLED=1
    fi
fi

if [ -z "$SK_INSTALLED" ]; then
    read -p "  Path to your Skript scripts folder (or press Enter to skip): " SK_PATH
    if [ -n "$SK_PATH" ] && [ -d "$SK_PATH" ]; then
        cp "$INSTALL_DIR/screen_display.sk" "$SK_PATH/screen_display.sk"
        echo "✔  Skript installed to $SK_PATH"
    else
        echo "  Skipped — copy $INSTALL_DIR/screen_display.sk into your server's"
        echo "  plugins/Skript/scripts/ folder manually."
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────"
echo "  Done!"
echo "─────────────────────────────────"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Make sure RCON is enabled in server.properties:"
echo "       enable-rcon=true"
echo "       rcon.password=$RCON_PASS"
echo ""
echo "  2. In-game, run:  /sk reload screen_display"
echo ""
echo "  3. Stand where you want the canvas, then run:  /screen"
echo ""
echo "  4. Start the stream:"
echo "       python3 $INSTALL_DIR/screen_capture.py"
echo ""
