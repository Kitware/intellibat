import serial
import numpy as np
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
CONFIG_KEYS = [SAMPLE_RATE_KEY, THRESHOLD_FREQ_KEY]

CHUNK_SECONDS = 2
BYTES_PER_SAMPLE = 2
SAMPLES_PER_CHUNK = SAMPLE_RATE * CHUNK_SECONDS
BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * BYTES_PER_SAMPLE

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
        SAMPLE_RATE = config[SAMPLE_RATE_KEY]
        THRESHOLD_FREQ = config[THRESHOLD_FREQ_KEY]


def record():
    print("Reading...")
    while True:
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


def main():
    setup()
    record()

if __name__ == "__main__":
    main()
