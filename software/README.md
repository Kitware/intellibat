# Intellibat Raspberry Pi 5 Service

## LED configuration

Use the `led_enabled` checkbox on the configuration website or set
`"led_enabled": false` in `/etc/intellibat/config.json` to turn the LED off.
Set it to `true` to turn the LED back on. The default is `true`, including
for existing configuration files that omit this option.

The service stays online and continues recording according to its schedule
while the LED is off. Changes apply on the next configuration reload (about
60 seconds), without restarting the service.

## Requirements

Install the following dependencies:

```sh
sudo apt update
sudo apt upgrade
sudo apt install git-lfs htop tmux vim sox ffmpeg bpytop portaudio19-dev
sudo apt install cmake python3 build-essential gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib

mkdir -p ~/code/
mkdir -p ~/venv/

python3 -m venv ~/venv/batai
source ~/venv/batai/bin/activate

pip install -r requirements.txt
```
