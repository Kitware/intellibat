import time

import serial

ser = serial.Serial('/dev/ttyAMA1', 115200, timeout=0.5)
time.sleep(0.5)  # Allow time for connection

try:
    while True:
        if ser.in_waiting > 0:
            data = ser.readline()
            try:
                data = data.decode('utf-8').rstrip()
                print(f'Received: {data}')
            except Exception:
                print('BAD ENCODING')
finally:
    ser.close()  # Close port
