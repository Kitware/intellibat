# Intellibat Pico ADC Streamer

Firmware for a Raspberry Pi Pico/RP2040 that samples an analog microphone sensor
through the onboard ADC and streams signed 16-bit PCM-style samples to a
Raspberry Pi over USB CDC serial.

The USB stream is framed so the Pi can verify byte alignment and detect drops:

- USB device: usually `/dev/ttyACM0` on the Raspberry Pi
- Frame header: `IBAT` magic, uint32 sequence, uint16 sample count, uint16 flags, uint32 dropped sample count
- Sample payload: little-endian signed 16-bit (`<i2`)
- Default sample rate: `384000` Hz
- Channels: one mono ADC channel

At 384 kHz, the Nyquist frequency is 192 kHz. The USB stream is about 768 KB/s
before USB serial and frame overhead.

## Wiring

Required:

- Pico USB to Raspberry Pi USB: power, data stream, and control commands
- Microphone analog output to Pico `GP26 / ADC0`
- Microphone ground to Pico `GND`
- Microphone power to the voltage required by the sensor board

Optional UART debug:

- Pico `GP0 / UART0 TX` to a USB-UART adapter RX, or to Pi UART RX if enabled
- Pico `GP1 / UART0 RX` only if you want to send UART input later

No extra control lines are required between the Pi and Pico. Streaming control is
handled with newline-terminated commands over the same USB CDC serial link.

## USB Commands

Send commands as ASCII text ending in `\n`:

- `START`: begin sampling and streaming
- `STOP`: stop streaming and clear queued samples
- `SET_SR:<hz>`: set sample rate, clamped to 128000-384000 Hz
- `STATUS`: emit status on the UART debug port

Debug/status responses intentionally go to UART so they do not corrupt the raw
USB sample stream consumed by the Python service.

The firmware boots with streaming stopped. The Pi service sends `STOP`,
`SET_SR:<hz>`, clears any stale USB input, and then sends `START` when the
recording window is active. The service strips frame headers before writing WAV
data and warns if USB frame sequences skip or the Pico reports dropped ADC
samples.

The Pico uses 2048-sample DMA-backed ADC capture blocks so USB writes do not
directly pace ADC sampling. On Pico 2/RP2350 builds, the default firmware keeps
96 capture blocks queued, which is about 0.51 seconds of backlog at 384 kHz. If
the Pi stops reading USB long enough for those blocks to fill, the
dropped-sample counter will increase.

The Pi recorder keeps a dedicated reader thread active while streaming so FFT
processing and WAV writes do not pause USB reads.

Frames are assembled into a contiguous binary buffer and written to USB in one
driver call. Byte-at-a-time USB writes are too slow for 384 kHz audio.

For reliable 384 kHz streaming, build optimized firmware. The CMake project
defaults to `Release` when no explicit build type is provided.

Before running the recorder, verify the stream from the Pi:

```sh
python3 software/utils/check_usb.py --port /dev/ttyACM0 --sample-rate 384000 --seconds 5
```

The observed sample rate should be close to 384000 Hz, `sequence_gaps` should be
0, and `pico_dropped_samples` should stay 0.

## Raspberry Pi 5 Configuration

First, enable UART in the firmware configuration file and and reboot the Raspberry Pi 5.

```sh
sudo cp boot_firmware_config.txt /boot/firmware/config.txt
reboot
```

## Build

Install the Pico SDK and toolchain, then build:

```sh
cd firmware/src

mkdir -p build
cd build

cmake ..
make -j4

cp adc.uf2 ../../
```

To flash, hold the Pico `BOOTSEL` button while plugging it in, then copy:

```sh
cp firmware/flash_nuke.uf2 /media/$USER/RP2350/

# Wait for device to remount

cp firmware/adc.uf2 /media/$USER/RP2350/
```

On Linux the mount path is commonly `/media/<user>/RP2350/`.

## Configuration

The default compile-time settings live in `CMakeLists.txt`:

- `ADC_GPIO=26`
- `ADC_CHANNEL=0`
- `DEFAULT_SAMPLE_RATE_HZ=384000`
- `DEBUG_UART_TX_PIN=0`
- `DEBUG_UART_RX_PIN=1`
- `DEBUG_UART_BAUD=115200`

If the microphone sensor is connected to another ADC-capable pin, update both
`ADC_GPIO` and `ADC_CHANNEL`:

- `GP26` is `ADC0`
- `GP27` is `ADC1`
- `GP28` is `ADC2`
