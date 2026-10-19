#!/bin/bash
#
# Locust installation script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Test manually: bash install.sh
#
set -e

echo "=== Installing Locust ==="

# Locust lives in a dedicated venv on a modern Python. The AL2023 system
# Python (3.9) with its pip 21.3 can no longer resolve prebuilt gevent wheels
# for current Locust releases and falls back to a source build that fails.
PYTHON_PKG="python3.11"
VENV_DIR="/opt/cloudstorm/locust-venv"
LOCUST_BIN="$VENV_DIR/bin/locust"

# Check if already installed
if [ -x "$LOCUST_BIN" ]; then
    echo "Locust already installed: $($LOCUST_BIN --version)"
    exit 0
fi

if ! command -v "$PYTHON_PKG" &> /dev/null; then
    echo "Installing $PYTHON_PKG..."
    sudo dnf install -y "$PYTHON_PKG" "$PYTHON_PKG-pip"
fi

echo "Creating virtualenv at $VENV_DIR..."
sudo mkdir -p "$(dirname "$VENV_DIR")"
sudo "$PYTHON_PKG" -m venv "$VENV_DIR"
sudo "$VENV_DIR/bin/pip" install --quiet --upgrade pip

# Install locust
echo "Installing locust via pip..."
sudo "$VENV_DIR/bin/pip" install --quiet locust

# Expose locust on the default PATH
sudo ln -sf "$LOCUST_BIN" /usr/local/bin/locust

# Verify installation
if [ -x "$LOCUST_BIN" ]; then
    echo "Locust installed successfully: $($LOCUST_BIN --version)"
else
    echo "ERROR: Locust installation failed"
    exit 1
fi

echo "=== Locust installation complete ==="
