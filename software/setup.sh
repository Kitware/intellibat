#!/bin/bash
#
#
# Validate user-provided device name
DEVICE_NAME=${INTELLIBAT_DEVICE_NAME:-Intellibat}
if [ ${#DEVICE_NAME} -gt 32 ]; then
    echo "Error: INTELLIBAT_DEVICE_NAME must be 32 characters or fewer." >&2
    exit 1
fi

if [[ ! "$DEVICE_NAME" =~ ^[A-Za-z0-9\ -_]+$ ]]; then
    echo "Error: INTELLIBAT_DEVICE_NAME may only contain letters, numbers, spaces, dashes, and underscores." >&2
    exit 1
fi

if [[ "$DEVICE_NAME" == " "* || "$DEVICE_NAME" == *" " ]]; then
    echo "Error: INTELLIBAT_DEVICE_NAME may not start or end with a space." >&2
    exit 1
fi

# Create an intellibat user
id -u intellibat &> /dev/null || sudo useradd --system --no-create-home intellibat
sudo usermod -aG plugdev,dialout,gpio intellibat

APP_DIR=/opt/intellibat/

# Set up /opt/intellibat. Set owner to current user
sudo mkdir -p $APP_DIR
sudo chown -R intellibat:intellibat $APP_DIR

sudo cp ./service.py $APP_DIR/main.py
sudo cp ./requirements-install.txt $APP_DIR/requirements.txt

sudo cp -r ../intellibat_config/ $APP_DIR/
sudo rm  -rf $APP_DIR/intellibat_config/src/*.egg-info

# Set up python environment with dependencies
pushd $APP_DIR/
sudo -u intellibat python -m venv ./venv
sudo -u intellibat ./venv/bin/python -m pip install --no-cache-dir --upgrade pip
sudo -u intellibat ./venv/bin/python -m pip install --no-cache-dir -r requirements.txt
popd

# Set up the configuration file
CONFIG_DIR=/etc/intellibat/

sudo usermod -aG intellibat $(whoami)

sudo mkdir -p $CONFIG_DIR
sudo chown -R root:intellibat $CONFIG_DIR
sudo chmod 775 $CONFIG_DIR

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    sudo cp ./default_config.json $CONFIG_DIR/config.json
fi
sudo chown root:intellibat $CONFIG_DIR/config.json
sudo chmod 664 $CONFIG_DIR/config.json

if [ ! -f "$CONFIG_DIR/device.json" ]; then
    echo "{ \"name\": \"$DEVICE_NAME\" }" | sudo tee $CONFIG_DIR/device.json > /dev/null
fi
sudo chown root:intellibat $CONFIG_DIR/device.json
sudo chmod 664 $CONFIG_DIR/device.json

# Set up the access point
sudo apt install avahi-daemon hostapd dnsmasq -y
sudo systemctl stop hostapd
sudo systemctl stop dnsmasq
sudo systemctl disable hostapd
sudo systemctl disable dnsmasq
if ! nmcli connection show intellibat-ap &> /dev/null; then
    sudo nmcli connection add type wifi ifname wlan0 con-name intellibat-ap autoconnect on ssid "$DEVICE_NAME"
fi
sudo nmcli connection modify intellibat-ap 802-11-wireless.mode ap ipv4.method shared ipv6.method disabled
sudo nmcli connection modify intellibat-ap wifi-sec.key-mgmt wpa-psk
sudo nmcli connection modify intellibat-ap wifi-sec.psk "${INTELLIBAT_AP_PASSWORD:-intellibat123}"

# Set the mDNS name for a stable configuration URL
# This is independent of the DEVICE_NAME used for access point
# naming and file names
sudo hostnamectl set-hostname intellibat

# Ensure the hostname resolves for intellbat.local configuration
if ! grep -qE '^127\.0\.1\.1[[:space:]]+intellibat([[:space:]]|$)' /etc/hosts then;
    sudo sed -i '/^127\.0\.1\.1[[:space:]]/d' /etc/hosts
    echo '127.0.1.1 intellibat' | sudo tee -a /etc/hosts > /dev/null
fi

# Set up the recording and config services
sudo cp ./intellibat.service /etc/systemd/system/intellibat.service
sudo cp ./intellibat_config.service /etc/systemd/system/intellibat_config.service
sudo systemctl daemon-reload

sudo systemctl enable intellibat
sudo systemctl restart intellibat

sudo systemctl enable intellibat_config
sudo systemctl restart intellibat_config
