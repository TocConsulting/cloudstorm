#!/bin/bash
#
# Artillery installation script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Test manually: bash install.sh
#
set -e

echo "=== Installing Artillery ==="

# Check if already installed
if command -v artillery &> /dev/null; then
    echo "Artillery already installed: $(artillery version)"
    exit 0
fi

# Artillery 2.x requires Node.js >= 22.18. AL2023 ships nodejs22 natively
# (the default "nodejs" package is v18 and Node 20 is EOL).
NODE_MAJOR=$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')
if [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 22 ]; then
    echo "Installing Node.js 22..."
    sudo dnf install -y nodejs22 nodejs22-npm
fi

echo "Node.js version: $(node --version)"
echo "npm version: $(npm --version)"

# Install Artillery globally
echo "Installing Artillery via npm..."
sudo npm install -g artillery

# Verify installation
if command -v artillery &> /dev/null; then
    echo "Artillery installed successfully: $(artillery version)"
else
    echo "ERROR: Artillery installation failed"
    exit 1
fi

echo "=== Artillery installation complete ==="
