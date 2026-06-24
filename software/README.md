# Intellibat Raspberry Pi 5 Service

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
