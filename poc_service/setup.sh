#!/bin/bash
#
#
# Create an intellibat user
id -u intellibat &> /dev/null || sudo useradd --system --no-create-home intellibat

APP_DIR=/opt/intellibat/

# Set up /opt/intellibat. Set owner to current user
sudo mkdir -p $APP_DIR
sudo chown -R intellibat:intellibat $APP_DIR

sudo cp ./service.py $APP_DIR/main.py
sudo cp ./requirements.txt $APP_DIR/requirements.txt

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
sudo chmod 664 $CONFIG_DIR/config.json

# Set up the service
sudo cp ./intellibat.service /etc/systemd/system/intellibat.service
sudo systemctl daemon-reload
sudo systemctl enable intellibat
sudo systemctl restart intellibat

