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

import atexit
import json
import os
import struct
import sys
import threading
import time
import wave
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from queue import Queue
from threading import Thread
from zoneinfo import ZoneInfo

import numpy as np
import serial
from astral import LocationInfo
from astral.sun import sun
from timezonefinder import TimezoneFinder

from intellibat_config import ConfigManager, ScheduleMode

# Hardware/serial related constants (future configurable)
PORT = '/dev/ttyACM0'
BAUD = 115200

INCOMING = Queue()

# Configuration
CONFIG_PATH = Path(os.getenv('INTELLIBAT_CONFIG_PATH', 'config.json'))
config_manager = ConfigManager.from_file(CONFIG_PATH)

CHUNK_SECONDS = 5
BYTES_PER_SAMPLE = 2
SAMPLES_PER_CHUNK = config_manager.config.sample_rate * CHUNK_SECONDS
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
os.makedirs(OUTPUT_DIR, exist_ok=True)

ser = serial.Serial(
    PORT, BAUD, timeout=SERIAL_TIMEOUT_SECONDS, write_timeout=SERIAL_TIMEOUT_SECONDS
)
time.sleep(1)


class RecordingState(Enum):
    IDLE = 1
    RECORDING = 2


class RecordingStateMachine:
    def __init__(self, config_manager, spectrogram_queue):
        self.state = RecordingState.IDLE
        self.config_manager = config_manager
        self.spectrogram_queue = spectrogram_queue
        self.current_recording = None
        self.filename = None
        self.chunks_written = 0

    @property
    def idle(self):
        return self.state == RecordingState.IDLE

    @property
    def recording(self):
        return self.state == RecordingState.RECORDING

    def begin_recording(self, current_recording, filename):
        if self.state == RecordingState.RECORDING:
            return
        self.state = RecordingState.RECORDING
        self.current_recording = current_recording
        self.filename = filename
        self.chunks_written = 0
        self.last_triggered = (
            time.monotonic()
        )  # assume recording starts with a bat call

    def stop_recording(self):
        print(
            f'Stopping recording for {self.filename}. Recorded {self.chunks_written} seconds'
        )
        if self.current_recording:
            self.current_recording.close()
            if self.filename:
                self.spectrogram_queue.put(self.filename)

        self.state = RecordingState.IDLE
        self.current_recording = None
        self.filename = None
        self.chunks_written = 0

    def handle_chunk(self, data, triggers):
        if self.state == RecordingState.IDLE:
            return

        if self.current_recording:
            self.current_recording.writeframes(data)
            self.chunks_written += 1

        if self.chunks_written >= self.config_manager.config.maximum_recording_length:
            print('Maximum file size reached...')
            self.stop_recording()
            return

        continuous_mode = not self.config_manager.config.triggered_recording
        if triggers or continuous_mode:
            self.last_triggered = time.monotonic()
        else:
            time_since_trigger = time.monotonic() - self.last_triggered
            if time_since_trigger > self.config_manager.config.trigger_window:
                print('Trigger window elapsed...')
                self.stop_recording()


class RecordingSchedule:
    def __init__(self, start_time, end_time):
        self._start_time = start_time
        self._end_time = end_time
        self._time_zone = None

    @property
    def start_time(self):
        return self._start_time

    @property
    def end_time(self):
        return self._end_time

    @property
    def time_zone(self):
        return self._time_zone

    def set_start_time(self, start_time):
        self._start_time = start_time

    def set_end_time(self, end_time):
        self._end_time = end_time


recording_schedule = RecordingSchedule(datetime.now().time(), datetime.now().time())


class LED(Thread):
    def __init__(self):
        from gpiozero import RGBLED

        Thread.__init__(self)
        self.name = 'led'
        self.daemon = True

        self.gpio_red = 17
        self.gpio_green = 27
        self.gpio_blue = 22

        # Smaller delay = smoother/faster updates
        self.step_delay = 0.02

        # Larger number = smoother color transition
        self.steps_per_cycle = 360
        assert isinstance(self.steps_per_cycle, int)
        assert self.steps_per_cycle > 0

        # Use active_high=True for common-cathode modules.
        # If colors are inverted or the LED is on when it should be off,
        # change this to active_high=False.
        self.led = RGBLED(
            red=self.gpio_red,
            green=self.gpio_green,
            blue=self.gpio_blue,
            active_high=True,
        )

        # Run the shutdown function to close all open things when
        # the process is terminated
        atexit.register(self.shutdown)

    def shutdown(self):
        if self.led:
            self.led.off()
        self.led = None

    def run(self):
        import colorsys

        while True:
            try:
                for step in range(self.steps_per_cycle):
                    hue = step / self.steps_per_cycle

                    # hsv_to_rgb returns red, green, blue values from 0.0 to 1.0
                    red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, 1.0)

                    if self.led:
                        self.led.color = (red, green, blue)

                    time.sleep(self.step_delay)
            except KeyboardInterrupt:
                break
            except Exception:
                pass


class UART(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.name = 'uart'
        self.daemon = True

        self.baud_rate = 115200
        self.uart1 = serial.Serial('/dev/ttyAMA1', self.baud_rate, timeout=0.5)
        self.enabled = True

        # Run the shutdown function to close all open things when
        # the process is terminated
        atexit.register(self.shutdown)

    def shutdown(self):
        self.enabled = False
        time.sleep(0.1)
        self.uart1.close()  # Close port

    def run(self):
        time.sleep(0.5)  # Allow time for connection
        while True:
            if self.enabled and self.uart1.in_waiting > 0:
                try:
                    line = self.uart1.readline()
                    message = line.decode('utf-8').rstrip()
                    print(f'[UART1] {message}')
                except Exception:
                    print('[UART1] ERROR: Failed to decode message')


class Spectrogram(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.name = 'spectrogram'
        self.daemon = True

    def next(self):
        return INCOMING.get(block=True)

    def run(self):
        import batbot

        while True:
            chunk_filepath = self.next()
            qsize = INCOMING.qsize()
            if qsize > 1:
                print(f'[spectrogram] Queue size {qsize}')

            _, compressed_paths, metadata_path, metadata = batbot.spectrogram.compute(
                chunk_filepath,
                fast_mode=True,
                quiet=True,
                debug=False,
            )
            print(f'Created: {compressed_paths}')


def update_chunk_dimensions():
    global SAMPLES_PER_CHUNK
    global BYTES_PER_CHUNK
    SAMPLES_PER_CHUNK = config_manager.config.sample_rate * CHUNK_SECONDS
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
    write_device_command(f'SET_SR:{config_manager.config.sample_rate}')
    ser.reset_input_buffer()
    CONFIGURED_SAMPLE_RATE = config_manager.config.sample_rate

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
    max_pending_bytes = (
        config_manager.config.sample_rate * BYTES_PER_SAMPLE * MAX_PENDING_SECONDS
    )

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
        except (OSError, KeyboardInterrupt):
            STREAM_READER_ERROR = None
            with SAMPLE_BUFFER_CONDITION:
                SAMPLE_BUFFER_CONDITION.notify_all()
            maybe_print_comm_warning('\n\nFinishing...')
            return
        except Exception as exc:
            STREAM_READER_ERROR = exc
            with SAMPLE_BUFFER_CONDITION:
                SAMPLE_BUFFER_CONDITION.notify_all()
            maybe_print_comm_warning(f'\n\nUSB stream reader stopped: {exc}')
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


def update_recording_schedule():
    config = config_manager.config
    if config.schedule_mode == ScheduleMode.CUSTOM:
        recording_schedule.set_start_time(config.start_time)
        recording_schedule.set_end_time(config.end_time)
    else:
        tz_finder = TimezoneFinder()
        tz_name = tz_finder.timezone_at(lat=config.latitude, lng=config.longitude)
        if not tz_name:
            print(
                'Could not determine time zone. Please update the config and restart.'
            )
            sys.exit(1)
        location = LocationInfo(
            latitude=config.latitude, longitude=config.longitude, timezone=tz_name
        )
        sun_info = sun(
            location.observer, date=date.today(), tzinfo=ZoneInfo(location.timezone)
        )
        sunset = sun_info['sunset']
        sunrise = sun_info['sunrise']
        if config.schedule_mode == ScheduleMode.SUNSET_TO_SUNRISE:
            recording_schedule.set_start_time(sunset.time())
            recording_schedule.set_end_time(sunrise.time())
        elif config.schedule_mode == ScheduleMode.SUNSET_MINUS_30_TO_SUNRISE_PLUS_30:
            recording_schedule.set_start_time((sunset - timedelta(minutes=30)).time())
            recording_schedule.set_end_time((sunrise + timedelta(minutes=30)).time())


def setup():
    print('Setting up...')
    # Try and find the settings file
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(CONFIG_PATH, 'w') as f:
            json.dump(
                {
                    'recording_format': 'full_spectrum',
                    'sample_rate': 256000,
                    'triggered_recording': True,
                    'minimum_trigger_frequency': 20,
                    'maximum_recording_length': 15,
                    'trigger_window': 5,
                    'save_noise_files': False,
                    'latitude': 0,
                    'longitude': 0,
                    'schedule_mode': 'custom',
                    'start_time': '17:00',
                    'end_time': '05:00',
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
        config_manager.update(config)
        update_recording_schedule()

        update_chunk_dimensions()
        if config_manager.config.sample_rate != CONFIGURED_SAMPLE_RATE:
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
    start_time = recording_schedule.start_time
    end_time = recording_schedule.end_time

    if start_time < end_time:
        return start_time <= now < end_time

    # Handle a range that crosses midnight
    return now >= start_time or now < end_time


def chunk_triggers(data):
    samples = np.frombuffer(data, dtype='<i2')

    fft = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(len(samples), d=1 / config_manager.config.sample_rate)

    magnitude = np.abs(fft)
    magnitude[0] = 0

    peak_idx = np.argmax(magnitude)
    peak_freq = freqs[peak_idx]

    return peak_freq > config_manager.config.minimum_trigger_frequency * 1000


def record():
    print('Reading...')
    recorder = RecordingStateMachine(
        config_manager=config_manager, spectrogram_queue=INCOMING
    )
    while True:
        try:
            maybe_reload_config()

            if should_record():
                if not DEVICE_STREAMING:
                    start_device_streaming()

                data = read_sample_chunk()
                if len(data) != BYTES_PER_CHUNK:
                    print('Incomplete read:', len(data))
                    continue

                triggers = chunk_triggers(data)

                if recorder.recording:
                    print(f'Recording in progress. Adding data to {recorder.filename}')
                    recorder.handle_chunk(data, triggers)
                else:  # Recorder is idle
                    continuous_mode = not config_manager.config.triggered_recording
                    if triggers or continuous_mode:
                        filename = os.path.join(
                            OUTPUT_DIR, f'chunk_{int(time.time())}.wav'
                        )
                        print(f'High frequency detected. Recording to file {filename}')
                        with wave.open(filename, 'wb') as wav_file:
                            wav_file.setnchannels(1)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(config_manager.config.sample_rate)
                            recorder.begin_recording(wav_file, filename)
                            recorder.handle_chunk(data, True)

                    INCOMING.put(filename)
                    print('Saved:', filename)
            else:
                if DEVICE_STREAMING:
                    stop_device_streaming()
                time.sleep(5)
        except KeyboardInterrupt:
            break


def main():
    indicator = LED()
    indicator.start()

    feed = UART()
    feed.start()

    renderer = Spectrogram()
    renderer.start()

    try:
        setup()
        record()
    finally:
        print('\n\nShutting Down...')
        if DEVICE_STREAMING:
            stop_device_streaming()

        time.sleep(1)

        ser.close()  # Close port

        time.sleep(1)


if __name__ == '__main__':
    main()
