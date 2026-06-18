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

import json
import os
import struct
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import serial

# Hardware/serial related constants (future configurable)
PORT = '/dev/ttyACM0'
BAUD = 115200

# Configurable settings
SAMPLE_RATE = 256000
THRESHOLD_FREQ = 1200
SAMPLE_RATE_KEY = 'sample_rate'
THRESHOLD_FREQ_KEY = 'threshold_freq'
RECORDING_START_TIME = datetime.strptime('17:00', '%H:%M').time()
RECORDING_END_TIME = datetime.strptime('05:30', '%H:%M').time()
START_TIME_KEY = 'start_time'
END_TIME_KEY = 'end_time'
CONFIG_KEYS = [SAMPLE_RATE_KEY, THRESHOLD_FREQ_KEY, START_TIME_KEY, END_TIME_KEY]

CHUNK_SECONDS = 2
BYTES_PER_SAMPLE = 2
SAMPLES_PER_CHUNK = SAMPLE_RATE * CHUNK_SECONDS
BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * BYTES_PER_SAMPLE

FRAME_MAGIC = b'IBAT'
FRAME_HEADER_FORMAT = '<4sIHHI'
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)
MAX_FRAME_SAMPLES = 4096
SERIAL_TIMEOUT_SECONDS = 1
MAX_PENDING_SECONDS = 10

RELOAD_INTERVAL = 60  # seconds
LAST_RELOAD_TIME = datetime.now()
LAST_RELOAD_MONOTONIC = time.monotonic()
DEVICE_STREAMING = False
CONFIGURED_SAMPLE_RATE = None
EXPECTED_FRAME_SEQUENCE = None
LAST_DROPPED_SAMPLES = 0
PENDING_SAMPLE_BYTES = bytearray()
LAST_COMM_WARNING_TIME = 0.0
STREAM_READER_THREAD = None
STREAM_READER_STOP = threading.Event()
STREAM_READER_ERROR = None
SAMPLE_BUFFER_CONDITION = threading.Condition()

OUTPUT_DIR = 'test_recordings'
CONFIG_PATH = Path('config.json')
os.makedirs(OUTPUT_DIR, exist_ok=True)

ser = serial.Serial(
    PORT, BAUD, timeout=SERIAL_TIMEOUT_SECONDS, write_timeout=SERIAL_TIMEOUT_SECONDS
)
time.sleep(3)


def update_chunk_dimensions():
    global SAMPLES_PER_CHUNK
    global BYTES_PER_CHUNK
    SAMPLES_PER_CHUNK = SAMPLE_RATE * CHUNK_SECONDS
    BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * BYTES_PER_SAMPLE


def reset_frame_tracking():
    global EXPECTED_FRAME_SEQUENCE
    global LAST_DROPPED_SAMPLES
    EXPECTED_FRAME_SEQUENCE = None
    LAST_DROPPED_SAMPLES = 0
    with SAMPLE_BUFFER_CONDITION:
        PENDING_SAMPLE_BYTES.clear()
        SAMPLE_BUFFER_CONDITION.notify_all()


def maybe_print_comm_warning(message: str):
    global LAST_COMM_WARNING_TIME
    now = time.monotonic()
    if now - LAST_COMM_WARNING_TIME >= 5:
        print(message)
        LAST_COMM_WARNING_TIME = now


def write_device_command(command: str, settle_seconds: float = 0.05):
    ser.write(f'{command}\n'.encode('ascii'))
    ser.flush()
    time.sleep(settle_seconds)


def stop_device_streaming():
    global DEVICE_STREAMING
    global STREAM_READER_THREAD

    STREAM_READER_STOP.set()
    write_device_command('STOP')
    if STREAM_READER_THREAD is not None:
        STREAM_READER_THREAD.join(timeout=SERIAL_TIMEOUT_SECONDS + 0.5)
        STREAM_READER_THREAD = None
    ser.reset_input_buffer()
    reset_frame_tracking()
    DEVICE_STREAMING = False


def start_device_streaming():
    global DEVICE_STREAMING
    global STREAM_READER_THREAD
    global STREAM_READER_ERROR

    ser.reset_input_buffer()
    reset_frame_tracking()
    STREAM_READER_ERROR = None
    STREAM_READER_STOP.clear()
    write_device_command('START', settle_seconds=0.02)
    STREAM_READER_THREAD = threading.Thread(target=stream_reader_loop, daemon=True)
    STREAM_READER_THREAD.start()
    DEVICE_STREAMING = True


def configure_device_sample_rate():
    global CONFIGURED_SAMPLE_RATE
    was_streaming = DEVICE_STREAMING

    stop_device_streaming()
    write_device_command(f'SET_SR:{SAMPLE_RATE}')
    ser.reset_input_buffer()
    CONFIGURED_SAMPLE_RATE = SAMPLE_RATE

    if was_streaming:
        start_device_streaming()


def read_exact(byte_count: int) -> bytes:
    data = ser.read(byte_count)
    if len(data) != byte_count:
        raise TimeoutError(
            f'Incomplete serial read: expected {byte_count}, got {len(data)}'
        )
    return data


def read_frame():
    window = bytearray()

    while True:
        byte = read_exact(1)
        window += byte
        if len(window) > len(FRAME_MAGIC):
            del window[0]
        if bytes(window) == FRAME_MAGIC:
            break

    header = FRAME_MAGIC + read_exact(FRAME_HEADER_SIZE - len(FRAME_MAGIC))
    magic, sequence, sample_count, flags, dropped_samples = struct.unpack(
        FRAME_HEADER_FORMAT, header
    )
    if magic != FRAME_MAGIC or sample_count == 0 or sample_count > MAX_FRAME_SAMPLES:
        maybe_print_comm_warning('Invalid frame header; resynchronizing USB stream')
        return read_frame()

    payload = read_exact(sample_count * BYTES_PER_SAMPLE)
    return sequence, sample_count, flags, dropped_samples, payload


def append_next_frame_samples():
    global EXPECTED_FRAME_SEQUENCE
    global LAST_DROPPED_SAMPLES

    sequence, sample_count, _flags, dropped_samples, payload = read_frame()

    if EXPECTED_FRAME_SEQUENCE is None:
        EXPECTED_FRAME_SEQUENCE = sequence

    if sequence != EXPECTED_FRAME_SEQUENCE:
        maybe_print_comm_warning(
            f'USB frame sequence gap: expected {EXPECTED_FRAME_SEQUENCE}, got {sequence}'
        )

    EXPECTED_FRAME_SEQUENCE = (sequence + 1) & 0xFFFFFFFF

    if dropped_samples != LAST_DROPPED_SAMPLES:
        maybe_print_comm_warning(
            f'Pico dropped ADC samples: {dropped_samples - LAST_DROPPED_SAMPLES} new, '
            f'{dropped_samples} total'
        )
        LAST_DROPPED_SAMPLES = dropped_samples

    expected_payload_len = sample_count * BYTES_PER_SAMPLE
    if len(payload) != expected_payload_len:
        raise RuntimeError(
            f'Invalid frame payload: expected {expected_payload_len}, got {len(payload)}'
        )

    append_sample_payload(payload)


def append_sample_payload(payload: bytes):
    max_pending_bytes = SAMPLE_RATE * BYTES_PER_SAMPLE * MAX_PENDING_SECONDS

    with SAMPLE_BUFFER_CONDITION:
        PENDING_SAMPLE_BYTES.extend(payload)
        if len(PENDING_SAMPLE_BYTES) > max_pending_bytes:
            overflow = len(PENDING_SAMPLE_BYTES) - max_pending_bytes
            overflow += overflow % BYTES_PER_SAMPLE
            del PENDING_SAMPLE_BYTES[:overflow]
            maybe_print_comm_warning(
                f'Pi processing backlog overflow: dropped {overflow // BYTES_PER_SAMPLE} buffered samples'
            )
        SAMPLE_BUFFER_CONDITION.notify_all()


def stream_reader_loop():
    global STREAM_READER_ERROR

    while not STREAM_READER_STOP.is_set():
        try:
            append_next_frame_samples()
        except TimeoutError:
            if DEVICE_STREAMING and not STREAM_READER_STOP.is_set():
                maybe_print_comm_warning(
                    'Timed out waiting for framed USB data from Pico'
                )
        except Exception as exc:
            STREAM_READER_ERROR = exc
            with SAMPLE_BUFFER_CONDITION:
                SAMPLE_BUFFER_CONDITION.notify_all()
            maybe_print_comm_warning(f'USB stream reader stopped: {exc}')
            return


def read_sample_chunk() -> bytes:
    with SAMPLE_BUFFER_CONDITION:
        while len(PENDING_SAMPLE_BYTES) < BYTES_PER_CHUNK:
            if STREAM_READER_ERROR is not None:
                raise RuntimeError('USB stream reader failed') from STREAM_READER_ERROR
            SAMPLE_BUFFER_CONDITION.wait(timeout=1)

        data = bytes(PENDING_SAMPLE_BYTES[:BYTES_PER_CHUNK])
        del PENDING_SAMPLE_BYTES[:BYTES_PER_CHUNK]
        return data


def validate_config(config: dict) -> bool:
    required_keys = CONFIG_KEYS
    missing = [k for k in required_keys if k not in config]
    for m in missing:
        print(f'Config missing key {m}.')
    return not missing


def setup():
    print('Setting up...')
    # Try and find the settings file
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(CONFIG_PATH, 'w') as f:
            json.dump(
                {
                    SAMPLE_RATE_KEY: 256000,
                    THRESHOLD_FREQ_KEY: 1200,
                    START_TIME_KEY: '17:00',
                    END_TIME_KEY: '05:30',
                },
                f,
                indent=2,
            )
        print(
            f'Config file created at {CONFIG_PATH}. Please review and restart service.'
        )
        sys.exit(1)
    # Config path exists
    with open(CONFIG_PATH) as f:
        config = json.load(f)
        config_valid = validate_config(config)
        if not config_valid:
            print('Invalid config. Please fix the errors and restart the service.')
            sys.exit(1)
        global SAMPLE_RATE
        global THRESHOLD_FREQ
        global RECORDING_START_TIME
        global RECORDING_END_TIME
        SAMPLE_RATE = config[SAMPLE_RATE_KEY]
        THRESHOLD_FREQ = config[THRESHOLD_FREQ_KEY]
        RECORDING_START_TIME = datetime.strptime(config[START_TIME_KEY], '%H:%M').time()
        RECORDING_END_TIME = datetime.strptime(config[END_TIME_KEY], '%H:%M').time()
        update_chunk_dimensions()
        if CONFIGURED_SAMPLE_RATE != SAMPLE_RATE:
            configure_device_sample_rate()

        global LAST_RELOAD_TIME
        global LAST_RELOAD_MONOTONIC
        LAST_RELOAD_TIME = datetime.now()
        LAST_RELOAD_MONOTONIC = time.monotonic()


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
    print('Reading...')
    while True:
        maybe_reload_config()

        if should_record():
            if not DEVICE_STREAMING:
                start_device_streaming()

            data = read_sample_chunk()
            if len(data) != BYTES_PER_CHUNK:
                print('Incomplete read:', len(data))
                continue
            samples = np.frombuffer(data, dtype='<i2')

            fft = np.fft.rfft(samples)
            freqs = np.fft.rfftfreq(len(samples), d=1 / SAMPLE_RATE)

            magnitude = np.abs(fft)
            magnitude[0] = 0

            peak_idx = np.argmax(magnitude)
            peak_freq = freqs[peak_idx]

            if peak_freq > THRESHOLD_FREQ:
                print(f'Peak: {int(peak_freq)} Hz -> KEEP')

                filename = os.path.join(OUTPUT_DIR, f'chunk_{int(time.time())}.wav')
                with wave.open(filename, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(data)
                print('Saved:', filename)
            else:
                print(f'Peak: {int(peak_freq)} Hz -> DISCARD')
        else:
            if DEVICE_STREAMING:
                stop_device_streaming()
            time.sleep(5)


def main():
    setup()
    record()


if __name__ == '__main__':
    main()
