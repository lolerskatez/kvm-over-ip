#!/bin/sh
set -e

echo "=== KVM-over-IP Setup for Alpine Linux ==="
echo ""

if [ "$(id -u)" != "0" ]; then
    echo "Error: This script must be run as root"
    exit 1
fi

echo "[1/7] Installing required packages..."
apk update
apk add --no-cache \
    python3 \
    py3-pip \
    ffmpeg \
    udev \
    openrc \
    linux-headers \
    build-base

echo "[2/7] Creating kvm user and group..."
if ! getent group kvm > /dev/null; then
    addgroup kvm
fi

if ! getent passwd kvm > /dev/null; then
    adduser -D -G kvm -h /var/lib/kvm -s /sbin/nologin kvm
fi

echo "[3/7] Installing Python dependencies..."
cd /opt/kvm-over-ip
python3 -m venv .venv
. /opt/kvm-over-ip/.venv/bin/activate
pip install --no-cache-dir -r /opt/kvm-over-ip/requirements.txt
deactivate

echo "[4/7] Setting up directories and permissions..."
mkdir -p /etc/kvm
mkdir -p /var/lib/kvm
mkdir -p /var/log/kvm-over-ip
mkdir -p /opt/kvm-over-ip

cp /opt/kvm-over-ip/config.json /etc/kvm/config.json
cp /opt/kvm-over-ip/users.json /etc/kvm/users.json

chown -R kvm:kvm /etc/kvm
chown -R kvm:kvm /var/lib/kvm
chown -R kvm:kvm /var/log/kvm-over-ip
chown -R kvm:kvm /opt/kvm-over-ip

chmod 750 /etc/kvm
chmod 750 /var/lib/kvm
chmod 750 /var/log/kvm-over-ip
chmod 750 /opt/kvm-over-ip

chmod 640 /etc/kvm/config.json
chmod 640 /etc/kvm/users.json

echo "[5/7] Installing udev rules..."
cp /opt/kvm-over-ip/udev/99-kvm-over-ip.rules /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger

echo "[6/7] Installing OpenRC service..."
cp /opt/kvm-over-ip/init.d/kvm-over-ip /etc/init.d/
chmod +x /etc/init.d/kvm-over-ip

echo "[7/7] Enabling service..."
rc-update add kvm-over-ip default

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Configuration files:"
echo "  - /etc/kvm/config.json (video/HID device settings)"
echo "  - /etc/kvm/users.json (user credentials)"
echo ""
echo "To start the service:"
echo "  rc-service kvm-over-ip start"
echo ""
echo "To view logs:"
echo "  tail -f /var/log/kvm-over-ip/app.log"
echo ""
echo "Default credentials:"
echo "  Username: admin"
echo "  Password: admin"
echo ""
echo "IMPORTANT: Change the default password immediately!"
echo "To change password, edit /etc/kvm/users.json with a new hashed password."
echo ""
