#!/bin/bash
#
# JMeter installation script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Test manually: bash install.sh
#
set -e

echo "=== Installing JMeter ==="

JMETER_VERSION="5.6.3"
JMETER_HOME="/opt/jmeter"

# Check if already installed
if [ -x "$JMETER_HOME/bin/jmeter" ]; then
    echo "JMeter already installed: $($JMETER_HOME/bin/jmeter --version 2>&1 | head -1)"
    exit 0
fi

# Install Java and gawk (needed for JTL percentile calculation) if not present
if ! command -v java &> /dev/null; then
    echo "Installing Java..."
    sudo dnf install -y java-17-amazon-corretto-headless
fi

if ! command -v gawk &> /dev/null; then
    echo "Installing gawk..."
    sudo dnf install -y gawk
fi

echo "Java version: $(java -version 2>&1 | head -1)"

# Download JMeter
echo "Downloading JMeter ${JMETER_VERSION}..."
cd /tmp

# Try multiple mirrors
JMETER_URL="https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz"
JMETER_MIRROR="https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz"

if ! curl -fsSL --connect-timeout 15 --max-time 120 -o jmeter.tgz "$JMETER_MIRROR" 2>/dev/null; then
    echo "Mirror failed, trying archive..."
    curl -fsSL --connect-timeout 15 --max-time 120 -o jmeter.tgz "$JMETER_URL"
fi

# Clean up any partial previous install
sudo rm -rf "$JMETER_HOME" "/opt/apache-jmeter-${JMETER_VERSION}"

# Extract
echo "Extracting JMeter..."
sudo tar -xzf jmeter.tgz -C /opt
sudo mv "/opt/apache-jmeter-${JMETER_VERSION}" "$JMETER_HOME"
rm -f jmeter.tgz

# Create symlink
sudo ln -sf "$JMETER_HOME/bin/jmeter" /usr/local/bin/jmeter

# Set permissions
sudo chmod +x "$JMETER_HOME/bin/"*

# Verify installation
if [ -x "$JMETER_HOME/bin/jmeter" ]; then
    echo "JMeter installed successfully"
    "$JMETER_HOME/bin/jmeter" --version 2>&1 | head -5
else
    echo "ERROR: JMeter installation failed"
    exit 1
fi

echo "=== JMeter installation complete ==="
