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

## Spectrogram and ML workers

Both workers run as background threads inside `intellibat.service`; there is
no separate spectrogram systemd unit. Completed WAVs go to `SPECTROGRAM_QUEUE`.
The spectrogram worker renders them with BatBot's headless Agg backend, then
puts each compressed image on `CLASSIFIER_QUEUE`. The classifier loads its
model inside its own thread and publishes one atomic `.results.json` per image.
Model loading cannot block recorder startup, and WAVs or empty batches are
never passed to the classifier.

Each failed job is logged and retried up to three attempts. A malformed WAV or
failed classification does not stop either worker or the recorder. After the
last attempt, the input stays on disk for diagnosis and recovery on the next
service restart. The Status tab shows worker activity, current file, completed
jobs, retries, and the last processing error. Logs are available with:

```sh
journalctl -u intellibat -f
```

At startup, a background scan requeues completed WAVs without complete
spectrogram products and images without valid ML results. Complete products
are reused. `.part` recordings remain untouched. With ML disabled, rendering
continues and the classifier pauses its pending work. Re-enabling ML in the
configuration resumes processing on the next configuration reload.

Deploy both `service.py` and `processing.py` into `/opt/intellibat/software`.
To run the hardware-independent pipeline regression tests from the repo root:

```sh
python -m unittest discover -s software/tests -v
```

## Reducing idle CPU use

The debug UART waits in a bounded serial read when no messages arrive and
backs off after I/O failures. It must not continuously poll `in_waiting`;
that previously kept a CPU core busy even outside the recording schedule.
The web interface and audio reader remain active independently.

For lower processing load, disable `machine_learning_enabled` in Settings.
This pauses classification; spectrogram generation continues. Disabling
`led_enabled` reduces LED consumption. A 256 kHz sample rate reduces data
volume compared with 384 kHz, but lowers the Nyquist frequency from 192 kHz to
128 kHz, so choose it only if that frequency range meets the recording needs.
