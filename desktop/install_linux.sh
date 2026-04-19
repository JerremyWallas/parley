#!/usr/bin/env bash
# Parley Desktop — Linux Setup (Ubuntu 22.04+ / Debian / Wayland or X11)
#
# Installs system dependencies, Python packages, and configures ydotool
# so the app can synthesize Ctrl+V into other windows on Wayland.
#
# Usage:
#   cd desktop && ./install_linux.sh
#
# After this script finishes, log out and back in once so the new
# 'input' group membership takes effect, then run: python3 main.py

set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This script is for Linux only." >&2
    exit 1
fi

if ! command -v apt >/dev/null 2>&1; then
    echo "Warning: apt not found. This script targets Debian/Ubuntu." >&2
    echo "Install these packages manually for your distro:" >&2
    echo "  ydotool wl-clipboard python3-tk python3-pip portaudio19-dev libgirepository1.0-dev gir1.2-ayatanaappindicator3-0.1" >&2
    exit 1
fi

echo "==> Installing system packages (sudo required)..."
sudo apt update
sudo apt install -y \
    ydotool \
    wl-clipboard \
    xclip \
    python3-tk \
    python3-pip \
    python3-venv \
    python3-gi \
    portaudio19-dev \
    libgirepository1.0-dev \
    gir1.2-ayatanaappindicator3-0.1 \
    gnome-shell-extension-appindicator

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "==> Installing Python dependencies..."
if [[ -d "$SCRIPT_DIR/venv" ]]; then
    echo "Using existing venv at $SCRIPT_DIR/venv"
else
    # --system-site-packages so the venv can see python3-gi for AppIndicator tray
    python3 -m venv --system-site-packages "$SCRIPT_DIR/venv"
fi
"$SCRIPT_DIR/venv/bin/pip" install --upgrade pip
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "==> Adding $USER to 'input' group (needed for evdev hotkey capture and ydotool)..."
if ! groups "$USER" | grep -qw input; then
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
else
    echo "Already in 'input' group."
    NEED_RELOGIN=0
fi

echo "==> Setting up ydotoold systemd user service..."
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/ydotoold.service" <<'EOF'
[Unit]
Description=ydotoold — uinput daemon for ydotool
After=default.target

[Service]
Type=simple
ExecStart=/usr/bin/ydotoold --socket-path=%t/.ydotool_socket --socket-own=%U:%G
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ydotoold.service || {
    echo "Warning: could not start ydotoold service. You may need to start it manually." >&2
}

# ydotool needs the socket path; export it for current shell and future sessions
SOCKET_LINE='export YDOTOOL_SOCKET="$XDG_RUNTIME_DIR/.ydotool_socket"'
if ! grep -qF "$SOCKET_LINE" "$HOME/.bashrc" 2>/dev/null; then
    echo "$SOCKET_LINE" >> "$HOME/.bashrc"
fi
if [[ -f "$HOME/.zshrc" ]] && ! grep -qF "$SOCKET_LINE" "$HOME/.zshrc"; then
    echo "$SOCKET_LINE" >> "$HOME/.zshrc"
fi

echo
echo "==========================================="
echo "  Parley setup complete."
echo "==========================================="
echo
if [[ "$NEED_RELOGIN" -eq 1 ]]; then
    echo "IMPORTANT: log out and back in once so the new 'input' group"
    echo "membership takes effect."
    echo
fi
echo "Then start Parley with:"
echo "  cd $SCRIPT_DIR"
echo "  ./venv/bin/python main.py"
echo
echo "If the system tray icon doesn't show up on GNOME, enable the"
echo "AppIndicator extension via the Extensions app or:"
echo "  gnome-extensions enable ubuntu-appindicators@ubuntu.com"
