#!/bin/bash
#
# k6 installation script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Test manually: bash install.sh
#
set -e

echo "=== Installing k6 ==="

# Check if already installed
if command -v k6 &> /dev/null; then
    echo "k6 already installed: $(k6 version)"
    exit 0
fi

# Install k6 from official repository
echo "Adding k6 repository..."
sudo dnf install -y https://dl.k6.io/rpm/repo.rpm 2>/dev/null || true

echo "Installing k6..."
sudo dnf install -y --nogpgcheck k6

# Verify installation
if command -v k6 &> /dev/null; then
    echo "k6 installed successfully: $(k6 version)"
else
    echo "ERROR: k6 installation failed"
    exit 1
fi

echo "=== k6 installation complete ==="
