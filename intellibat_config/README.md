# IntelliBat device interface

The interface runs locally on the Raspberry Pi at port 8000. The navigation
order is **Settings**, **System status**, **Recorded data**. Opening `/` lands
on the center System status tab. CSS, JavaScript, and charts are served by the
device; an internet connection is not needed to use the interface.

## System status

The dashboard refreshes every 10 seconds and reports:

- Recorder heartbeat, active recording, last received audio, last completed
  recording, session recording count, schedule, and applied configuration time.
- Spectrogram and classifier queue lengths, worker thread health, current job,
  completed/skipped jobs, retries, last processing error, last completion,
  startup recovery counts, buffered audio, and dropped Pico samples.
- The recording and configuration services' systemd states, results, PIDs,
  restart counts, memory use, and active-since timestamps.
- Raspberry Pi model, hostname, architecture, OS/kernel, CPU frequency,
  CPU utilization, core count, load averages, and uptime.
- RAM and swap use, thermal sensors, fan RPM, current under-voltage/throttling
  flags, and flags recorded since boot.
- Battery capacity, charging status, health, voltage, current, power,
  remaining/full energy and charge, temperature, cycle count, and time to
  empty/full when the Linux driver provides those readings. Values that the
  hardware does not report remain unavailable; battery capacity is not inferred
  from the Pi supply voltage.
- Raspberry Pi PMIC voltage/current channels when `vcgencmd` supports them.
- Network interfaces, addresses, received/sent bytes, clock synchronization,
  and the presence of the microphone and debug serial devices.
- OS and recording storage capacity, used/free bytes, write access, and inodes.

The recorder writes an atomic heartbeat every five seconds. A heartbeat older
than 20 seconds is marked stale; a streaming recorder with no audio for ten
seconds is marked stalled. These are independent of systemd's process state.
CPU utilization needs two samples, so the first reading can be unavailable.
Recorder heartbeat and audio ages use monotonic time when available, so a
manual wall-clock correction does not conceal stalled activity.

### Set device time remotely

Under **System status → Set device time**, choose **Use this computer's time**
to copy the browser computer's clock, or enter a date/time and select
**Set entered time**. Manual entry uses the browser's timezone, which is shown
beside the field; a UTC preview makes the exact instant explicit. The device
timezone is shown separately and is preserved. The request sets the Pi's system
clock while the recording service remains online.

Manual setting disables automatic network synchronization. **Enable network
time** restores it; reaching a time server and completing synchronization can
take time. The interface distinguishes enabled synchronization from a clock
that is actually synchronized. It shows the Pi clock, timezone, and approximate
difference from the browser clock. Clock-setting errors are displayed; if a
manual setting fails, the app attempts to restore the original NTP setting.

New recordings use the corrected time. Existing timestamps are unchanged, and
filename collisions after setting the clock backwards receive a unique suffix
instead of overwriting data. Earlier recordings remain exportable even when
their modification dates are now in the future, and copy estimate expiration
uses elapsed monotonic time.

Clock control requires the included `49-intellibat-clock.rules` polkit rule,
which grants the `intellibat` account systemd time-setting and NTP control.
It uses [systemd's timedatectl interface](https://github.com/systemd/systemd/blob/main/man/timedatectl.xml)
with explicit UTC timestamps and non-interactive authorization. Like the other
configuration controls, it is available to clients on the device's local web
interface; it does not require an SSH session. Unsupported hosts show the
control as unavailable.

Battery units follow the [Linux power supply interface](https://cdn.kernel.org/doc/html/latest/power/power_supply_class.html).
Pi firmware measurements follow the [Raspberry Pi documentation](https://www.raspberrypi.com/documentation/computers/os.html)
and [PMIC documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html).
PMIC channels do not represent every peripheral's power consumption.

## Recorded data

The data tab inventories the recording directory and the existing `output`
spectrogram directory. It reports total files and bytes, WAV recordings,
images, ML result files, files in progress, and the collection's time range.
The inventory refreshes every 30 seconds; parsed JSON is cached by size and
modification time to reduce disk reads. Large per-call metadata is not retained
in the dashboard's cache.

The scrollable recent-images panel shows up to ten spectrograms, their recording
times, sizes, durations, sample rates, ML window counts, and top-five predictions.
It recognizes BatBot's `.NNofNN.compressed.jpg`, `.results.json`, and
`.metadata.json` output. Missing or incomplete ML results are reported as such.

The species histogram counts each WAV once, using its highest-confidence
prediction when that confidence is at least 25%, excluding NOISE winners.
Multiple ML image tiles are combined using their `window_count` weights and
full scores when available. Noise, low-confidence, and unclassified recordings
are reported separately.

The day-selectable, horizontally scrollable activity chart follows
`scripts/species_timeline.py`: 15-minute bins, alternatives at 35% opacity,
and species bar heights of 100% (>50% confidence), 50% (>=25%), or 10% (>=10%).
Numbered bars and total detections count only qualifying top-1 recordings.
NOISE still competes when selecting the winner. Bins use POSIX time so the
repeated hour at daylight saving time remains distinct. The default display
timezone is America/Los_Angeles.

## Copy to external storage

1. Plug a USB or other removable drive into the Pi and click **Refresh** beside
   Storage medium. The device detects filesystems using `lsblk`; OS/boot disks
   and filesystems containing the recording sources are excluded.
2. Select the filesystem. Available and total storage, filesystem type, mount
   point, and write access are shown. **Mount selected drive** uses `udisksctl`
   for unmounted media; it does not format drives or change partitions.
3. Choose the drive root, choose an existing immediate child folder, or create
   one new folder. The name must be one level deep, without slashes or leading
   dots. Symlinks are not accepted as destinations.
4. Click **Preview copy size**. The background estimate reports new files/bytes,
   already copied files, conflicts, unfinished files, and available space.
   The space check also reserves room for filesystem allocation and manifests.
   Estimates expire after 15 minutes.
5. Click **Start copy** after reviewing the estimate. A byte progress bar,
   completed file count, current filename, transfer speed, and remaining-time
   estimate update while copying. The copy runs on the Pi and continues when
   navigating between tabs or closing the browser. Reopening the data tab
   reconnects to the current job.

Exports preserve the source layout beneath two namespaces:

```text
Selected folder/
  recordings/     # WAVs and anything stored beside them
  spectrograms/   # Existing output directory, including images and ML JSON
  .intellibat-copy-manifest.json
```

The manifest remembers source and destination size/mtime signatures and each
copied file's SHA-256. Unchanged signatures allow subsequent estimates to skip
old copies without rereading their audio. Existing same-sized files without a
matching manifest entry are compared using SHA-256. Different files at the same
path are reported as conflicts and preserved; choose another folder to export
those files. Source modification times are preserved, subject to the target
filesystem's timestamp precision.

Files created after an estimate are picked up by the next estimate. Late ML
results are copied on the next run even when the WAV was copied earlier.
There is no overwrite or deletion mode. Source symlinks, hidden files, `.part`,
and `.tmp` files are excluded. Files modified in the last two seconds are also
deferred. New WAVs are recorded to `.wav.part` and renamed after closing the
WAV header; ML results are published atomically. An abandoned `.part` file after
a power loss is left untouched for manual recovery.

Each copied file is written to a temporary file, flushed, and published without
replacing an existing filename. Removal, I/O failures, source changes, and
cancellation stop the job and preserve completed files. Preview again to copy
the remaining files. A web-service restart loses its in-memory job status and
estimate, but completed copies remain usable and are recognized next time.
Only one estimate/copy runs at once; run this web application with **one Uvicorn
worker**, as in the included systemd unit. Safely unmount/eject the drive on
the Pi before disconnecting it.

## Installation and paths

Install the updated Python package and systemd units on the Pi. For a fresh
installation, `software/setup.sh` also installs UDisks and a narrow polkit rule
allowing the `intellibat` account to mount non-system removable filesystems.
It adds the `video` group for firmware telemetry access.

To update an existing installation without reconfiguring its network, run
these commands from the updated repository on the Pi:

```sh
sudo -u intellibat /opt/intellibat/venv/bin/python -m pip install -e ./intellibat_config
sudo apt install -y udisks2
sudo usermod -aG video intellibat
sudo install -m 644 software/49-intellibat-storage.rules /etc/polkit-1/rules.d/49-intellibat-storage.rules
sudo install -m 644 software/49-intellibat-clock.rules /etc/polkit-1/rules.d/49-intellibat-clock.rules
sudo cp software/intellibat.service software/intellibat_config.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart intellibat intellibat_config
```

The updated recorder code must also be present at `/opt/intellibat/software/service.py`
and `/opt/intellibat/software/processing.py`.
Mounting and write permissions depend on the filesystem. FAT/exFAT drives
mounted by UDisks use the requesting account. On filesystems with Unix ownership,
grant the `intellibat` account write access to the export directory on the Pi.
The app reports permission failures without attempting a privileged copy.
The polkit rule follows [UDisks authorization actions](https://storaged.org/doc/udisks2-api/latest/udisks-polkit-actions.html).

Both services must use the same paths:

| Environment variable | Installed systemd value |
| --- | --- |
| `INTELLIBAT_CONFIG_PATH` | `/etc/intellibat/config.json` |
| `INTELLIBAT_RECORDINGS_PATH` | `/opt/intellibat/test_recordings` |
| `INTELLIBAT_SPECTROGRAMS_PATH` | `/opt/intellibat/output` |
| `INTELLIBAT_TELEMETRY_PATH` | `/run/intellibat/status.json` |
| `INTELLIBAT_TIMEZONE` | `America/Los_Angeles` (web display default) |

The recording unit creates `/run/intellibat` using systemd `RuntimeDirectory`.
For local development, defaults are `config.json`, `test_recordings`, `output`,
and `runtime/status.json` beneath the current working directory. Copy
`software/default_config.json` to the configured JSON path before starting.
Hardware values remain unavailable on computers without the corresponding
Linux or Pi interfaces.

## Verification

With the package dependencies and `httpx` installed:

```sh
PYTHONPATH=intellibat_config/src python -m unittest discover -s intellibat_config/tests -v
```

Tests cover tab routing, the existing LED setting, inventory and ML aggregation,
parity with the species timeline, telemetry units and stale heartbeats, atomic
WAV completion, incremental exports, conflicts, cancellation, removal, changed
sources, insufficient space, and destination traversal/symlink rejection.
Clock tests cover UTC conversion, request validation, NTP recovery, permission
failures, and preserving recordings and exports across backward clock changes.
