"""
A proof-of-concept python script that reads data over USB.

Incoming data is audio data (see stream.py for an example).

As chunks of data come in, they are minimally processed to determine if
those chunks should be saved to disc or not. Currently that processing
involves checking the frequency with the most energy (via FFT). If the
frequency is above a certain threshold, then the chunk is saved.

This script is also responsible for reading from a configuration file. This
is meant to simulate the configuration that the Intellibat system will
eventually support. Here are some example configuration options:

Supported:
    - threshold_freq: the target frequency necessary for saving audio to begin

Not supported:
    - threshold_energy: target amplitude for frequencies in some band. could be a better way to
        determine the presence of high-frequency bat calls
    - sample_rate: preferred sample rate for processed audio and of saved audio chunks
    - chunk_size: size of audio chunks to save to disc
"""

import serial
import numpy as np
from datetime import datetime
import time
import wave
import os
import json
from pathlib import Path
import sys

# Hardware/serial related constants (future configurable)
PORT = "/dev/ttyACM0"
BAUD = 115200

# Configurable settings
SAMPLE_RATE = 8000
THRESHOLD_FREQ = 1200
SAMPLE_RATE_KEY = "sample_rate"
THRESHOLD_FREQ_KEY = "threshold_freq"
RECORDING_START_TIME = datetime.strptime("17:00", "%H:%M").time()
RECORDING_END_TIME = datetime.strptime("05:30", "%H:%M").time()
START_TIME_KEY = "start_time"
END_TIME_KEY = "end_time"
CONFIG_KEYS = [SAMPLE_RATE_KEY, THRESHOLD_FREQ_KEY, START_TIME_KEY, END_TIME_KEY]

CHUNK_SECONDS = 2
BYTES_PER_SAMPLE = 2
SAMPLES_PER_CHUNK = SAMPLE_RATE * CHUNK_SECONDS
BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * BYTES_PER_SAMPLE

RELOAD_INTERVAL = 60  # seconds
LAST_RELOAD_TIME = datetime.now()
LAST_RELOAD_MONOTONIC = time.monotonic()

OUTPUT_DIR = "/home/mikenagler/dev/batai/data/test_recordings"
CONFIG_PATH = Path("/etc/intellibat/config.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

ser = serial.Serial(PORT, BAUD)
time.sleep(3)


def validate_config(config: dict) -> bool:
    required_keys = CONFIG_KEYS
    missing = [k for k in required_keys if k not in config]
    for m in missing:
        print(f"Config missing key {m}.")
    return not missing

def setup():
    print("Setting up...")
    # Try and find the settings file
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(CONFIG_PATH, "w") as f:
            json.dump({
                SAMPLE_RATE_KEY: 8000,
                THRESHOLD_FREQ_KEY: 1200,
                START_TIME_KEY: "17:00",
                END_TIME_KEY: "05:30",
            }, f, indent=2)
        print(f"Config file created at {CONFIG_PATH}. Please review and restart service.")
        sys.exit(1)
    # Config path exists
    with open(CONFIG_PATH) as f:
        config = json.load(f)
        config_valid = validate_config(config)
        if not config_valid:
            print("Invalid config. Please fix the errors and restart the service.")
            sys.exit(1)
        global SAMPLE_RATE
        global THRESHOLD_FREQ
        global RECORDING_START_TIME
        global RECORDING_END_TIME
        SAMPLE_RATE = config[SAMPLE_RATE_KEY]
        THRESHOLD_FREQ = config[THRESHOLD_FREQ_KEY]
        RECORDING_START_TIME = datetime.strptime(config[START_TIME_KEY], "%H:%M").time()
        RECORDING_END_TIME = datetime.strptime(config[END_TIME_KEY], "%H:%M").time()

        global LAST_RELOAD_TIME
        LAST_RELOAD_TIME = datetime.now()


def maybe_reload_config():
    if time.monotonic() - LAST_RELOAD_MONOTONIC > RELOAD_INTERVAL:
        setup()


def should_record():
    now = datetime.now().time()

    if RECORDING_START_TIME < RECORDING_END_TIME:
        return RECORDING_START_TIME <= now < RECORDING_END_TIME

    # Handle a range that crosses midnight
    return now >= RECORDING_START_TIME or now < RECORDING_END_TIME


def record():
    print("Reading...")
    while True:
        maybe_reload_config()

        if should_record():
            data = ser.read(BYTES_PER_CHUNK)
            if len(data) != BYTES_PER_CHUNK:
                print("Incomplete read:", len(data))
                continue
            samples = np.frombuffer(data, dtype="<i2")

            fft = np.fft.rfft(samples)
            freqs = np.fft.rfftfreq(len(samples), d=1/SAMPLE_RATE)

            magnitude = np.abs(fft)
            magnitude[0] = 0

            peak_idx = np.argmax(magnitude)
            peak_freq = freqs[peak_idx]

            if peak_freq > THRESHOLD_FREQ:
                print(f"Peak: {int(peak_freq)} Hz -> KEEP")

                filename = os.path.join(OUTPUT_DIR, f"chunk_{int(time.time())}.wav")
                with wave.open(filename, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(data)
                print("Saved:", filename)
            else:
                print(f"Peak: {int(peak_freq)} Hz -> DISCARD")
        else:
            ser.reset_input_buffer()
            time.sleep(5)






def main():
    setup()
    record()

if __name__ == "__main__":
    main()
