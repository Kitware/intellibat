import argparse
import struct
import time

import serial

FRAME_MAGIC = b'IBAT'
FRAME_HEADER_FORMAT = '<4sIHHI'
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)
BYTES_PER_SAMPLE = 2
MAX_FRAME_SAMPLES = 4096


def read_exact(ser, byte_count):
    data = ser.read(byte_count)
    if len(data) != byte_count:
        raise TimeoutError(f'Expected {byte_count} bytes, got {len(data)}')
    return data


def write_command(ser, command, settle_seconds=0.05):
    ser.write(f'{command}\n'.encode('ascii'))
    ser.flush()
    time.sleep(settle_seconds)


def read_frame(ser):
    window = bytearray()

    while True:
        window += read_exact(ser, 1)
        if len(window) > len(FRAME_MAGIC):
            del window[0]
        if bytes(window) == FRAME_MAGIC:
            break

    header = FRAME_MAGIC + read_exact(ser, FRAME_HEADER_SIZE - len(FRAME_MAGIC))
    magic, sequence, sample_count, flags, dropped_samples = struct.unpack(
        FRAME_HEADER_FORMAT,
        header,
    )
    if magic != FRAME_MAGIC or sample_count == 0 or sample_count > MAX_FRAME_SAMPLES:
        raise ValueError(
            f'Invalid frame header: magic={magic!r}, sample_count={sample_count}'
        )

    payload = read_exact(ser, sample_count * BYTES_PER_SAMPLE)
    return sequence, sample_count, flags, dropped_samples, payload


def main():
    parser = argparse.ArgumentParser(
        description='Check Intellibat Pico framed ADC stream.'
    )
    parser.add_argument('--port', default='/dev/ttyACM0')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--sample-rate', type=int, default=256000)
    parser.add_argument('--seconds', type=float, default=5.0)
    args = parser.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=2)
    time.sleep(1)

    try:
        write_command(ser, 'STOP')
        ser.reset_input_buffer()
        write_command(ser, f'SET_SR:{args.sample_rate}')
        ser.reset_input_buffer()
        write_command(ser, 'START', settle_seconds=0.02)

        start = time.monotonic()
        deadline = start + args.seconds
        frames = 0
        samples = 0
        sequence_gaps = 0
        expected_sequence = None
        last_dropped_samples = 0

        while time.monotonic() < deadline:
            sequence, sample_count, _flags, dropped_samples, _payload = read_frame(ser)
            if expected_sequence is None:
                expected_sequence = sequence
            elif sequence != expected_sequence:
                sequence_gaps += 1

            expected_sequence = (sequence + 1) & 0xFFFFFFFF
            last_dropped_samples = dropped_samples
            frames += 1
            samples += sample_count

        elapsed = time.monotonic() - start
        observed_rate = samples / elapsed
        observed_byte_rate = (samples * BYTES_PER_SAMPLE) / elapsed

        print(f'frames: {frames}')
        print(f'samples: {samples}')
        print(f'elapsed_seconds: {elapsed:.3f}')
        print(f'observed_sample_rate_hz: {observed_rate:.1f}')
        print(f'observed_payload_byte_rate: {observed_byte_rate:.1f}')
        print(f'sequence_gaps: {sequence_gaps}')
        print(f'pico_dropped_samples: {last_dropped_samples}')
    finally:
        write_command(ser, 'STOP')
        ser.close()


if __name__ == '__main__':
    main()
