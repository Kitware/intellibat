"""
A dummy script meant to run on a Raspberry Pi Pico.

Streams sinewave data at a random frequency between 220Hz and
1760Hz, switching frequncies at 5-second intervals.

Also contains logic to accept commands to start/stop streaming,
or to stream at different sample rates.
"""
import math
import time
import sys
import select
import struct
import random

# Setup
time.sleep(2)
print("booted")
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)


# Configuraion
streaming = True
cmd_buffer = ""
max_cmd_len = 13
frequency = 440
phase = 0
sample_rate = 8000
switch_interval = 5000
last_switch = time.ticks_ms()


# Timing
sample_interval_us = int(1000000 / sample_rate)
next_sample_time = time.ticks_us()


# Metrics
sample_count = 0
last_report = time.ticks_ms()

pack = struct.pack
stdout_write = sys.stdout.buffer.write
_poll = poll.poll


# Main loop
while True:
    events = _poll(0)
    
    if events:
        ch = sys.stdin.read(1)
        if ch:
            if ch == "\n":
                line = cmd_buffer.strip()
                cmd_buffer = ""
                if line.endswith("START"):
                    streaming = True
                    print("STREAMING")
                elif line.endswith("STOP"):
                    streaming = False
                    print("STOPPED")
                elif "SET_SR:" in line:
                    new_val = line.split(":")[-1]
                    try:
                        sr = int(new_val)
                        sample_rate = sr
                        print("New sample rate:", sample_rate)
                    except:
                        print("INVALID SR")
            else:
                cmd_buffer += ch
                if len(cmd_buffer) > max_cmd_len:
                    cmd_buffer = cmd_buffer[-max_cmd_len:]
    if streaming:
        now = time.ticks_us()
        if time.ticks_diff(now, next_sample_time) >= 0:
            value = math.sin(phase)
            phase += 2 * math.pi * frequency / sample_rate
            if phase > 2 * math.pi:
                phase -= 2 * math.pi
            sample = int(value * 32767)
            stdout_write(struct.pack("<h", sample))
            sample_count += 1
            next_sample_time = time.ticks_add(next_sample_time, sample_interval_us)
            
    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, last_switch) >= switch_interval:
        frequency = random.randint(220, 1760)
        last_switch = now_ms
