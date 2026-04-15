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

# Set up the service
sudo cp ./intellibat.service /etc/systemd/system/intellibat.service
sudo systemctl daemon-reload
sudo systemctl restart intellibat
