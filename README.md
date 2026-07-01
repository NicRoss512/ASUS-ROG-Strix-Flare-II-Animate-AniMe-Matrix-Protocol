# ASUS ROG Strix Flare II Animate AniMe Matrix Protocol Notes

Reverse-engineering notes and Linux tools for the **ASUS ROG Strix Flare II Animate** keyboard AniMe Matrix display.

This document describes the protocol discovered from USBPcap captures, the physical LED mapping, and the Python scripts used to display a clock and test/draw on the matrix.

> Device tested: `ASUSTeK ROG STRIX FLARE II ANIMATE`  
> USB VID:PID: `0b05:19fc`  
> Firmware/device revision seen in captures: `bcdDevice 3.14`

---

## Status

Working:

- Write frames to the AniMe Matrix from Linux.
- Display a live HH:MM clock.
- Control brightness by per-pixel intensity.
- Paint/test individual LEDs in a Tkinter GUI.
- Full 312 LED framebuffer access, including the leftmost/top-left LEDs.

Not implemented yet:

- Import GIF/image files directly in the Linux tool.
- Exact replication of Armoury Crate animation editor features.
- Any persistent/on-board storage programming.

---

## Important safety notes

Do **not** use the laptop AniMe Matrix protocol on this keyboard.

The following protocol families were tested and are **not** the correct protocol for this keyboard:

- `0x5E C0 02 ...` AniMe Matrix laptop packets.
- HID `SET_REPORT` control transfers with report id `0x5e`.
- `0xEC ...` motherboard/OLED-controller style packets.

Some of those commands caused the keyboard to freeze, reset, or make interface 4 time out.

If the keyboard hangs during experiments:

1. Unplug both USB connectors from the PC.
2. Wait 10-15 seconds.
3. Plug it back in.
4. If needed, hold **Fn + Esc** for about 10-15 seconds to reset the keyboard.

The final scripts in this repository use only the discovered safe frame write:

```text
1024-byte interrupt OUT frame on interface 4 / endpoint 0x07
frame[0:2] = 60 81
```

No control transfers are used.

---

## Linux device layout

On the tested Linux system the keyboard enumerates as a composite HID device:

```text
VID:PID: 0b05:19fc
Product: ROG STRIX FLARE II ANIMATE
Manufacturer: ASUSTeK
```

Typical `hidapi` enumeration:

```text
iface=0 usage_page=0x0001 usage=0x0006 -> /dev/hidraw1  keyboard
iface=1 usage_page=0xff00 usage=0x0001 -> /dev/hidraw2  vendor, 64-byte reports
iface=2 multiple collections             -> /dev/hidraw3  consumer/system/vendor/mouse
iface=3 usage_page=0x0001 usage=0x0006 -> /dev/hidraw4  keyboard
iface=4 usage_page=0xff02 usage=0x0001 -> /dev/hidraw5  vendor, 1024-byte reports
```

The AniMe Matrix frames are written to **interface 4**, usually `/dev/hidraw5`.

From the USB descriptor:

```text
Interface 4:
  HID class, vendor-defined usage page 0xff02
  Endpoint IN:  0x86, interrupt, 1024 bytes
  Endpoint OUT: 0x07, interrupt, 1024 bytes
```

The report descriptor for interface 4 declares 1024-byte input/output reports and no numbered report IDs:

```text
06 02 ff 09 01 a1 01
09 02 75 08 96 00 04 15 00 26 ff 00 81 02
09 03 75 08 96 00 04 15 00 26 ff 00 91 02
c0
```

---

## Udev permissions

A useful udev rule for non-root access:

```bash
sudo tee /etc/udev/rules.d/72-rog-flare-animate.rules >/dev/null <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="19fc", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0b05", ATTR{idProduct}=="19fc", MODE="0660", TAG+="uaccess"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug/replug the keyboard.

---

## Python dependencies

Arch Linux:

```bash
sudo pacman -S python-hidapi tk
```

Other distributions / virtualenv:

```bash
python -m pip install hidapi
```

The paint GUI requires Tkinter. Package names vary by distro, for example:

```bash
sudo apt install python3-tk
```

---

## Frame protocol

### Transport

Use `hidapi` and open the HID device whose `interface_number == 4`.

Write exactly **1024 bytes** with `hid.write(frame)`.

Important: do **not** prepend an extra `0x00` HID Report ID byte. The USBPcap capture shows exactly 1024 bytes sent to endpoint `0x07 OUT`.

### Frame format

The final frame format discovered from `fill.cap` is:

```text
offset  size  meaning
------  ----  ------------------------------------
0       1     0x60
1       1     0x81
2       2     0x00 0x00, observed zero/reserved
4       312   LED brightness values, one byte per LED
316     rest  zero padding up to 1024 bytes
```

So the complete frame is:

```text
60 81 00 00 [312 bytes LED brightness] [zero padding to 1024]
```

### Clear frame

```python
frame = bytearray(1024)
frame[0:2] = b"\x60\x81"
h.write(bytes(frame))
```

### Full white frame

```python
frame = bytearray(1024)
frame[0:2] = b"\x60\x81"
frame[4:4+312] = b"\xff" * 312
h.write(bytes(frame))
```

### Per-pixel brightness

There does not appear to be a separate brightness command in this protocol. Brightness is controlled by the byte value of each LED:

```text
0x00 = off
0x01..0xfe = dim to bright
0xff = full brightness
```

The clock script exposes this as a percent value:

```text
--brightness 0..100
```

which is converted to:

```python
raw = round(percent * 255 / 100)
```

### Minimal Python sender

```python
import hid

VID = 0x0B05
PID = 0x19FC
IFACE = 4

path = next(d["path"] for d in hid.enumerate(VID, PID)
            if d.get("interface_number") == IFACE)

h = hid.device()
h.open_path(path)

frame = bytearray(1024)
frame[0:2] = b"\x60\x81"
frame[4:4+312] = b"\xff" * 312

# Exactly 1024 bytes. No leading Report-ID byte.
h.write(bytes(frame))
h.close()
```

---

## Physical LED layout

The physical AniMe Matrix has 312 LEDs arranged as 24 staggered rows.

Row lengths, top to bottom:

```python
PHYSICAL_ROW_COUNTS = [
    19, 18, 18, 17, 17, 16, 16, 15, 15, 14, 14, 13,
    13, 12, 12, 11, 11, 10, 10, 9, 9, 8, 8, 7,
]
```

The sum is exactly 312.

Rows are staggered diagonally. For mapping purposes, an integer "global column" offset works well:

```python
def physical_row_offset(row: int) -> int:
    # row is zero-based
    # row 0 -> 0
    # rows 1/2 -> 1
    # rows 3/4 -> 2
    # etc.
    return (row + 1) // 2
```

### Raw index order

The framebuffer order is not simple row-major.

It is pair/interleaved by physical rows:

```text
row pair 0: rows 2 / 1
row pair 1: rows 4 / 3
row pair 2: rows 6 / 5
...
row pair 11: rows 24 / 23
```

Inside each global column, the lower row of the pair is emitted first, then the upper row. Points outside the row length are skipped.

Python mapping generator:

```python
PHYSICAL_ROW_COUNTS = [
    19, 18, 18, 17, 17, 16, 16, 15, 15, 14, 14, 13,
    13, 12, 12, 11, 11, 10, 10, 9, 9, 8, 8, 7,
]

LED_COUNT = 312


def physical_row_offset(row: int) -> int:
    return (row + 1) // 2


def build_physical_order() -> list[tuple[int, int]]:
    """Return raw_index -> (physical_row, physical_col), both zero-based."""
    order = []
    for pair in range(len(PHYSICAL_ROW_COUNTS) // 2):
        lower = 2 * pair + 1
        upper = 2 * pair
        for gcol in range(19):
            for row in (lower, upper):
                col = gcol - physical_row_offset(row)
                if 0 <= col < PHYSICAL_ROW_COUNTS[row]:
                    order.append((row, col))
    assert len(order) == LED_COUNT
    assert len(set(order)) == LED_COUNT
    return order
```

### Calibration points

The mapping above matches these observed scan points. Rows/dots below are **1-based**:

```text
raw index 0   -> row 1,  dot 1
raw index 1   -> row 2,  dot 1
raw index 2   -> row 1,  dot 2
raw index 10  -> row 1,  dot 6
raw index 11  -> row 2,  dot 6
raw index 21  -> row 2,  dot 11
raw index 61  -> row 3,  dot 13
raw index 111 -> row 7,  dot 4
raw index 161 -> row 10, dot 13
raw index 211 -> row 14, dot 10
raw index 261 -> row 19, dot 1
raw index 291 -> row 22, dot 6
raw index 310 -> row 24, dot 7
raw index 311 -> row 23, dot 8
```

The early confusion came from using `FB_OFFSET = 15`, which effectively skipped the first 11 LEDs. `fill.cap` proved the correct framebuffer start is `FB_OFFSET = 4`.

---

## Scripts

### `rog_flare2_clock_v3.py`

Live clock renderer for the keyboard AniMe Matrix.

Features:

- Displays current system time in `HH:MM` format.
- Blinking colon by default.
- Tuned custom font for readability on the diagonal matrix.
- User brightness control.
- Safe protocol: direct 1024-byte `60 81` frame writes only.

Example usage:

```bash
python rog_flare2_clock_v3.py
```

Run one test frame:

```bash
python rog_flare2_clock_v3.py --once --text 12:34
```

Brightness:

```bash
python rog_flare2_clock_v3.py -b 15     # dim
python rog_flare2_clock_v3.py -b 35     # default
python rog_flare2_clock_v3.py -b 100    # full brightness
```

Raw brightness value:

```bash
python rog_flare2_clock_v3.py --raw-brightness 64
```

Disable colon blinking:

```bash
python rog_flare2_clock_v3.py --no-blink
```

Clear the matrix:

```bash
python rog_flare2_clock_v3.py --clear
```

List render presets:

```bash
python rog_flare2_clock_v3.py --list-presets
```

Default preset:

```text
flare
```

The best readable clock settings found on the tested keyboard were:

```text
xs = 0,6,14,20
row_shifts = 0,3,4,3,0
```

Note: the clock script was originally tuned against Armoury Crate's system-clock placement. If you want full-matrix drawing or exact physical testing, use the paint GUI, which uses the final full framebuffer offset `4`.

---

### `rog_flare2_matrix_paint.py`

Tkinter GUI for painting and testing the AniMe Matrix.

Features:

- Draw / erase / toggle LEDs with the mouse.
- Auto-send frames while drawing.
- Brightness slider.
- Clear / Fill / Invert / Checker / Diagonal patterns.
- Raw index scanner.
- Physical calibrated view using the final 312 LED mapping.
- Save/load patterns as JSON.

Run:

```bash
python rog_flare2_matrix_paint.py
```

Controls:

```text
Left click / drag   draw
Right click / drag  erase
Middle click        toggle
Space               send current frame
c                   clear
```

Modes:

```text
Logical 32x10
Physical calibrated
Physical row-major
```

Recommended mode for real hardware testing:

```text
Physical calibrated
```

Useful tests:

1. Press **Fill**. All 312 LEDs should light.
2. Enter a raw index and press **Light only**.
3. Press **Scan raw** to walk the framebuffer order.

The GUI uses the final full framebuffer:

```text
FB_OFFSET = 4
frame[4 + raw_index] = brightness
```

---

### `rog_flare2_replay_capture.py`

Small protocol validation script. It replays the first captured Armoury Crate clock frames from `clock.cap`.

Useful to confirm that the transport and basic `60 81` frame write work on a machine.

Examples:

```bash
python rog_flare2_replay_capture.py --once a
python rog_flare2_replay_capture.py --once b
python rog_flare2_replay_capture.py --loop
python rog_flare2_replay_capture.py --clear
```

Frames `a` and `b` are the same clock capture with the colon off/on.

---

### `parse_usbpcap.py`

Development helper used to parse USBPcap `.cap` files without `tshark`.

It extracts USBPcap records, device descriptors, interface descriptors, and non-empty payloads for the ASUS device.

Example:

```bash
python parse_usbpcap.py clock.cap > summary.txt
```

This script is only for reverse-engineering/debugging and is not needed at runtime.

---

## Captures used for reverse engineering

### System clock capture

Armoury Crate was set to system-clock mode. The matrix showed `02:43` and the colon blinked. The capture contained repeated 1024-byte frames:

```text
60 81 ...
```

Only a small subset of bytes were non-zero because only the clock glyphs were lit.

This capture first revealed:

- Interface 4 / endpoint `0x07 OUT`.
- 1024-byte frames.
- Prefix `60 81`.
- Per-pixel brightness bytes.

### Fill capture

Armoury Crate was used to fill/light all LEDs. The capture contained:

```text
60 81 00 00 ff ff ff ...
```

The full-white frame had:

```text
312 bytes of 0xff from offset 4 to offset 315 inclusive
```

This proved the correct framebuffer offset:

```text
FB_OFFSET = 4
```

### GIF capture

An animated GIF capture showed the same frame format, but with varying grayscale byte values instead of only `0x00`/`0xff`.

This confirmed that animation frames are just repeated `60 81` framebuffer writes.

---

## Systemd user service example

Install the clock script somewhere permanent:

```bash
mkdir -p ~/.local/bin
cp rog_flare2_clock_v3.py ~/.local/bin/rog_flare2_clock.py
chmod +x ~/.local/bin/rog_flare2_clock.py
```

Create service:

```bash
mkdir -p ~/.config/systemd/user
nano ~/.config/systemd/user/rog-flare-clock.service
```

Example service:

```ini
[Unit]
Description=ROG Strix Flare II Animate AniMe Matrix Clock
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python /home/user/.local/bin/rog_flare2_clock.py -b 25
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now rog-flare-clock.service
```

Check logs:

```bash
journalctl --user -u rog-flare-clock.service -f
```

---

## Troubleshooting

### `interface 4 not found`

Check that the keyboard is connected and visible:

```bash
lsusb | grep -i 0b05
```

List HID devices with Python:

```python
import hid
for d in hid.enumerate(0x0b05, 0x19fc):
    print(d.get("interface_number"), d.get("path"), hex(d.get("usage_page", 0)))
```

You should see `interface_number == 4`.

### Permission denied

Install the udev rule above, reload rules, and replug the keyboard.

As a temporary test:

```bash
sudo python rog_flare2_clock_v3.py --once --text 12:34
```

### Matrix does not update

Make sure no other process is holding the HID device. Close Armoury Crate if passing the keyboard through a VM, and stop other test scripts.

Try a clear frame:

```bash
python rog_flare2_matrix_paint.py
# press Connect, then Clear
```

or:

```bash
python rog_flare2_clock_v3.py --clear
```

### Keyboard resets or HID timeouts

Do not run old probe scripts that use control transfers or `0x5E`/`0xEC` protocols.

Recover by unplugging/replugging. If needed, hold **Fn + Esc** for about 10-15 seconds.

---

## License / credits

These notes are based on community reverse engineering and USBPcap captures from a real ROG Strix Flare II Animate keyboard.

No ASUS proprietary code is included. The protocol description is derived from observed USB traffic and clean-room experimentation.
