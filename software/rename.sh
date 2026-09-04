#!/bin/bash

if [ $# -ne 1 ]; then
    echo "Usage: ./rename.sh DEVICE_NAME"
    exit 1
fi

DEVICE_NAME=$1
CONFIG_DIR="/etc/intellibat"

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

if [ ! -f "$CONFIG_DIR/device.json" ]; then
    echo "device.json doesn't exist. Run ./setup.sh before renaming the device."
    exit 1
fi
if ! nmcli connection show intellibat-ap &> /dev/null; then
    echo "The intellibat access point isn't set up. Run ./setup.sh before renaming the device."
    exit 1
fi

echo "{ \"name\": \"$DEVICE_NAME\" }" | sudo tee $CONFIG_DIR/device.json > /dev/null
sudo chown root:intellibat $CONFIG_DIR/device.json
sudo chmod 664 $CONFIG_DIR/device.json

sudo nmcli connection modify intellibat-ap ssid "$DEVICE_NAME"

