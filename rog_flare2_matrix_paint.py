#!/usr/bin/env python3
"""
ROG Strix Flare II Animate AniMe Matrix paint/test GUI.

Protocol discovered from USBPcap:
  - HID interface 4 (usually /dev/hidraw5)
  - interrupt OUT endpoint packet is 1024 bytes
  - first bytes: 60 81
  - framebuffer starts at byte offset 4
  - no HID Report-ID prefix

GUI modes:
  - Logical 32x10: the mapping used by the clock script (idx = y*32+x)
  - Physical 312: visual wedge-like 312 LED layout, row-major raw index order

Requirements:
  sudo pacman -S python-hidapi tk
or:
  python -m pip install hidapi
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:  # pragma: no cover
    print("tkinter is not available. On Arch install: sudo pacman -S tk", file=sys.stderr)
    raise

VID = 0x0B05
PID = 0x19FC
IFACE = 4
FRAME_SIZE = 1024
PREFIX = bytes([0x60, 0x81])
FB_OFFSET = 4
LED_COUNT = 312
LOGICAL_COLS = 32
LOGICAL_ROWS = 10  # 32*10 = 320, last 8 cells are disabled

# Physical hole layout observed from product photos / Hough detection.
# Sum is exactly 312.
PHYSICAL_ROW_COUNTS = [
    19, 18, 18, 17, 17, 16, 16, 15, 15, 14, 14, 13,
    13, 12, 12, 11, 11, 10, 10, 9, 9, 8, 8, 7,
]


def physical_row_offset(row: int) -> int:
    # Integer global-column offset for the staggered physical rows:
    # row 0 -> 0, rows 1/2 -> 1, rows 3/4 -> 2, etc.
    return (row + 1) // 2


def build_physical_calibrated_order() -> list[tuple[int, int]]:
    # raw_index -> (physical_row, physical_col).
    # IMPORTANT: fill.cap showed that the real framebuffer starts at byte 4:
    #   bytes 4..315 = 312 LED brightness values.
    # The previous GUI used byte 15, so old GUI index N is real raw index N+11.
    # With FB_OFFSET=4 the complete mapping is simple row-pair/interleaved:
    #   raw 0  -> row 1 dot 1
    #   raw 1  -> row 2 dot 1
    #   raw 2  -> row 1 dot 2
    #   raw 3  -> row 2 dot 2
    #   ...
    #   raw 11 -> row 2 dot 6  (this was old GUI index 0)
    #   raw 21 -> row 2 dot 11 (old GUI index 10)
    #
    # For every physical row pair, the lower row goes first, then the upper row:
    #   rows 2/1, then 4/3, then 6/5, ... up to 24/23.
    order: list[tuple[int, int]] = []
    for pair in range(len(PHYSICAL_ROW_COUNTS) // 2):
        lower = 2 * pair + 1
        upper = 2 * pair
        for gcol in range(19):
            for row in (lower, upper):
                col = gcol - physical_row_offset(row)
                if 0 <= col < PHYSICAL_ROW_COUNTS[row]:
                    order.append((row, col))
    if len(order) != LED_COUNT or len(set(order)) != LED_COUNT:
        raise RuntimeError(f"bad calibrated physical mapping: {len(order)} / {len(set(order))}")
    return order


PHYSICAL_CALIBRATED_ORDER = build_physical_calibrated_order()


def brightness_to_raw(percent: int) -> int:
    percent = max(0, min(100, int(percent)))
    return round(percent * 255 / 100)


def grey(v: int) -> str:
    v = max(0, min(255, int(v)))
    return f"#{v:02x}{v:02x}{v:02x}"


class FlareTransport:
    def __init__(self, iface: int = IFACE):
        self.iface = iface
        self.h = None
        self.path = None

    def connect(self) -> str:
        import hid  # type: ignore

        matches = [d for d in hid.enumerate(VID, PID) if d.get("interface_number") == self.iface]
        if not matches:
            raise RuntimeError(
                f"ROG Strix Flare II Animate interface {self.iface} not found. "
                f"Check udev permissions or run as root."
            )
        self.close()
        self.path = matches[0]["path"]
        self.h = hid.device()
        self.h.open_path(self.path)
        return repr(self.path)

    def close(self) -> None:
        if self.h is not None:
            try:
                self.h.close()
            except Exception:
                pass
        self.h = None

    def write(self, frame: bytes) -> int:
        if self.h is None:
            self.connect()
        if len(frame) != FRAME_SIZE:
            raise ValueError(f"frame must be {FRAME_SIZE} bytes, got {len(frame)}")
        n = self.h.write(frame)  # IMPORTANT: no leading Report ID byte
        if n < 0:
            err = ""
            try:
                err = self.h.error() or ""
            except Exception:
                pass
            raise OSError(f"hid.write failed: {err}")
        return n


class MatrixPaintApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ROG Flare II Animate AniMe Matrix Paint")
        self.geometry("980x720")
        self.minsize(850, 560)

        self.transport = FlareTransport(IFACE)
        self.active: Set[int] = set()
        self.cell_items: Dict[int, int] = {}  # canvas item -> raw index
        self.idx_items: Dict[int, int] = {}   # raw index -> canvas item
        self.disabled_items: Set[int] = set()
        self.paint_value = True
        self.pending_send: Optional[str] = None
        self.scan_job: Optional[str] = None
        self.scan_saved: Optional[Set[int]] = None
        self.scan_index = 0

        self.mode_var = tk.StringVar(value="Physical calibrated")
        self.brightness_var = tk.IntVar(value=35)
        self.auto_send_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Not connected")
        self.raw_index_var = tk.StringVar(value="0")
        self.scan_delay_var = tk.IntVar(value=80)

        self._build_ui()
        self.redraw_grid()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Mode:").pack(side=tk.LEFT)
        mode = ttk.Combobox(
            top,
            textvariable=self.mode_var,
            values=["Logical 32x10", "Physical calibrated", "Physical row-major"],
            width=16,
            state="readonly",
        )
        mode.pack(side=tk.LEFT, padx=(4, 12))
        mode.bind("<<ComboboxSelected>>", lambda _e: self.redraw_grid())

        ttk.Label(top, text="Brightness:").pack(side=tk.LEFT)
        bright = ttk.Scale(
            top,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self.brightness_var,
            command=lambda _v: self.on_brightness_change(),
            length=150,
        )
        bright.pack(side=tk.LEFT, padx=4)
        self.brightness_label = ttk.Label(top, width=5, text="35%")
        self.brightness_label.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Checkbutton(top, text="Auto send", variable=self.auto_send_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(top, text="Connect", command=self.connect).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Send", command=self.send_current).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Clear", command=self.clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Fill", command=self.fill).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Invert", command=self.invert).pack(side=tk.LEFT, padx=2)

        second = ttk.Frame(self, padding=(8, 0, 8, 8))
        second.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(second, text="Checker", command=self.checker).pack(side=tk.LEFT, padx=2)
        ttk.Button(second, text="Diagonal", command=self.diagonal).pack(side=tk.LEFT, padx=2)
        ttk.Label(second, text="Raw index:").pack(side=tk.LEFT, padx=(18, 4))
        ttk.Entry(second, textvariable=self.raw_index_var, width=6).pack(side=tk.LEFT)
        ttk.Button(second, text="Light only", command=self.light_raw_index).pack(side=tk.LEFT, padx=2)
        ttk.Label(second, text="Scan ms:").pack(side=tk.LEFT, padx=(18, 4))
        ttk.Entry(second, textvariable=self.scan_delay_var, width=5).pack(side=tk.LEFT)
        ttk.Button(second, text="Scan raw", command=self.start_scan).pack(side=tk.LEFT, padx=2)
        ttk.Button(second, text="Stop scan", command=self.stop_scan).pack(side=tk.LEFT, padx=2)
        ttk.Button(second, text="Save", command=self.save_pattern).pack(side=tk.RIGHT, padx=2)
        ttk.Button(second, text="Load", command=self.load_pattern).pack(side=tk.RIGHT, padx=2)

        help_frame = ttk.Frame(self, padding=(8, 0, 8, 4))
        help_frame.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(
            help_frame,
            text="Left click/drag = draw, right click/drag = erase, middle click = toggle. "
                 "Logical mode uses idx=y*32+x. Physical calibrated uses the scan-derived raw index mapping.",
        ).pack(side=tk.LEFT)

        self.canvas = tk.Canvas(self, bg="#151515", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=8)

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)

        self.canvas.bind("<ButtonPress-1>", lambda e: self.paint_at_event(e, True))
        self.canvas.bind("<B1-Motion>", lambda e: self.paint_at_event(e, True))
        self.canvas.bind("<ButtonPress-3>", lambda e: self.paint_at_event(e, False))
        self.canvas.bind("<B3-Motion>", lambda e: self.paint_at_event(e, False))
        self.canvas.bind("<ButtonPress-2>", self.toggle_at_event)
        self.bind("<space>", lambda _e: self.send_current())
        self.bind("c", lambda _e: self.clear())

    # ---------- Drawing/mapping ----------

    def redraw_grid(self) -> None:
        self.canvas.delete("all")
        self.cell_items.clear()
        self.idx_items.clear()
        self.disabled_items.clear()

        mode = self.mode_var.get()
        if mode == "Physical calibrated":
            self._draw_physical_grid(calibrated=True)
        elif mode == "Physical row-major":
            self._draw_physical_grid(calibrated=False)
        else:
            self._draw_logical_grid()
        self.refresh_cells()

    def _draw_logical_grid(self) -> None:
        pitch = 26
        r = 9
        margin = 24
        self.canvas.config(scrollregion=(0, 0, margin * 2 + LOGICAL_COLS * pitch, margin * 2 + LOGICAL_ROWS * pitch))
        for y in range(LOGICAL_ROWS):
            for x in range(LOGICAL_COLS):
                idx = y * LOGICAL_COLS + x
                cx = margin + x * pitch
                cy = margin + y * pitch
                item = self.canvas.create_rectangle(
                    cx - r, cy - r, cx + r, cy + r,
                    fill="#242424", outline="#555555", width=1,
                )
                if idx < LED_COUNT:
                    self.cell_items[item] = idx
                    self.idx_items[idx] = item
                    self.canvas.create_text(cx, cy + 15, text=str(idx), fill="#5d5d5d", font=("TkDefaultFont", 6))
                else:
                    self.disabled_items.add(item)
                    self.canvas.itemconfig(item, fill="#101010", outline="#303030")

    def _draw_physical_grid(self, calibrated: bool = True) -> None:
        pitch = 26
        y_step = 20
        r = 8
        margin_x = 32
        margin_y = 28
        max_x = 0

        if calibrated:
            # raw_index -> physical row/column from scan-calibrated order.
            raw_to_pos = PHYSICAL_CALIBRATED_ORDER
        else:
            # Old/simple visualization: raw_index follows physical row-major order.
            raw_to_pos = []
            for row, count in enumerate(PHYSICAL_ROW_COUNTS):
                for col in range(count):
                    raw_to_pos.append((row, col))

        for idx, (row, col) in enumerate(raw_to_pos):
            # The real holes drift right by about half a cell every row.
            x0 = margin_x + row * (pitch * 0.52)
            y = margin_y + row * y_step
            x = x0 + col * pitch
            item = self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill="#242424", outline="#555555", width=1,
            )
            self.cell_items[item] = idx
            self.idx_items[idx] = item
            if idx % 10 == 0 or idx in (0, 1, 299, 300):
                self.canvas.create_text(x, y + 14, text=str(idx), fill="#5d5d5d", font=("TkDefaultFont", 6))
            max_x = max(max_x, x)
        self.canvas.config(scrollregion=(0, 0, max_x + margin_x, margin_y * 2 + len(PHYSICAL_ROW_COUNTS) * y_step))

    def refresh_cells(self) -> None:
        raw = brightness_to_raw(self.brightness_var.get())
        on = grey(max(raw, 24)) if raw else "#050505"
        off = "#242424"
        for idx, item in self.idx_items.items():
            self.canvas.itemconfig(item, fill=on if idx in self.active else off)
        self.brightness_label.config(text=f"{self.brightness_var.get()}%")

    def event_to_index(self, event) -> Optional[int]:
        item = self.canvas.find_closest(event.x, event.y)
        if not item:
            return None
        item_id = item[0]
        return self.cell_items.get(item_id)

    def paint_at_event(self, event, value: bool) -> None:
        idx = self.event_to_index(event)
        if idx is None:
            return
        if value:
            self.active.add(idx)
        else:
            self.active.discard(idx)
        self.refresh_cells()
        self.schedule_send()

    def toggle_at_event(self, event) -> None:
        idx = self.event_to_index(event)
        if idx is None:
            return
        if idx in self.active:
            self.active.remove(idx)
        else:
            self.active.add(idx)
        self.refresh_cells()
        self.schedule_send()

    # ---------- Frame/protocol ----------

    def build_frame(self) -> bytes:
        frame = bytearray(FRAME_SIZE)
        frame[0:2] = PREFIX
        val = brightness_to_raw(self.brightness_var.get())
        for idx in self.active:
            if 0 <= idx < LED_COUNT:
                off = FB_OFFSET + idx
                if off < FRAME_SIZE:
                    frame[off] = val
        return bytes(frame)

    def send_current(self) -> None:
        self.pending_send = None
        try:
            n = self.transport.write(self.build_frame())
            self.status_var.set(f"Sent {n} bytes, active LEDs: {len(self.active)}, brightness: {self.brightness_var.get()}%")
        except Exception as exc:
            self.status_var.set(f"Send failed: {exc}")

    def schedule_send(self) -> None:
        if not self.auto_send_var.get():
            return
        if self.pending_send is not None:
            self.after_cancel(self.pending_send)
        self.pending_send = self.after(35, self.send_current)

    # ---------- Actions ----------

    def connect(self) -> None:
        try:
            path = self.transport.connect()
            self.status_var.set(f"Connected: {path}")
        except Exception as exc:
            self.status_var.set(f"Connect failed: {exc}")
            messagebox.showerror("Connect failed", str(exc))

    def clear(self) -> None:
        self.stop_scan(restore=False)
        self.active.clear()
        self.refresh_cells()
        self.send_current()

    def fill(self) -> None:
        self.stop_scan(restore=False)
        self.active = set(range(LED_COUNT))
        self.refresh_cells()
        self.schedule_send()

    def invert(self) -> None:
        self.stop_scan(restore=False)
        self.active = set(range(LED_COUNT)) - self.active
        self.refresh_cells()
        self.schedule_send()

    def checker(self) -> None:
        self.stop_scan(restore=False)
        self.active = {i for i in range(LED_COUNT) if i % 2 == 0}
        self.refresh_cells()
        self.schedule_send()

    def diagonal(self) -> None:
        self.stop_scan(restore=False)
        if self.mode_var.get().startswith("Physical"):
            active = set()
            idx = 0
            for row, count in enumerate(PHYSICAL_ROW_COUNTS):
                for col in range(count):
                    if (row + col) % 4 == 0:
                        active.add(idx)
                    idx += 1
            self.active = active
        else:
            self.active = {
                y * LOGICAL_COLS + x
                for y in range(LOGICAL_ROWS)
                for x in range(LOGICAL_COLS)
                if y * LOGICAL_COLS + x < LED_COUNT and (x - y) % 6 == 0
            }
        self.refresh_cells()
        self.schedule_send()

    def light_raw_index(self) -> None:
        self.stop_scan(restore=False)
        try:
            idx = int(self.raw_index_var.get(), 0)
        except ValueError:
            messagebox.showerror("Bad index", "Raw index must be integer, e.g. 42 or 0x2a")
            return
        if not (0 <= idx < LED_COUNT):
            messagebox.showerror("Bad index", f"Index must be 0..{LED_COUNT - 1}")
            return
        self.active = {idx}
        self.refresh_cells()
        self.send_current()

    def start_scan(self) -> None:
        self.stop_scan(restore=True)
        self.scan_saved = set(self.active)
        self.scan_index = 0
        self._scan_step()

    def _scan_step(self) -> None:
        self.active = {self.scan_index}
        self.raw_index_var.set(str(self.scan_index))
        self.refresh_cells()
        self.send_current()
        self.scan_index = (self.scan_index + 1) % LED_COUNT
        delay = max(10, int(self.scan_delay_var.get() or 80))
        self.scan_job = self.after(delay, self._scan_step)

    def stop_scan(self, restore: bool = True) -> None:
        if self.scan_job is not None:
            try:
                self.after_cancel(self.scan_job)
            except Exception:
                pass
        self.scan_job = None
        if restore and self.scan_saved is not None:
            self.active = set(self.scan_saved)
            self.scan_saved = None
            self.refresh_cells()
            self.send_current()

    def on_brightness_change(self) -> None:
        self.brightness_label.config(text=f"{self.brightness_var.get()}%")
        self.refresh_cells()
        self.schedule_send()

    # ---------- Save/load ----------

    def save_pattern(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save pattern",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        data = {
            "active": sorted(self.active),
            "brightness": self.brightness_var.get(),
            "mode": self.mode_var.get(),
            "protocol": "rog-flare2-60-81-offset4",
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved {path}")

    def load_pattern(self) -> None:
        path = filedialog.askopenfilename(
            title="Load pattern",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.active = {int(i) for i in data.get("active", []) if 0 <= int(i) < LED_COUNT}
        if "brightness" in data:
            self.brightness_var.set(max(0, min(100, int(data["brightness"]))))
        if data.get("mode") in ("Logical 32x10", "Physical calibrated", "Physical row-major", "Physical 312"):
            self.mode_var.set("Physical calibrated" if data["mode"] == "Physical 312" else data["mode"])
            self.redraw_grid()
        self.refresh_cells()
        self.send_current()

    def on_close(self) -> None:
        self.stop_scan(restore=False)
        self.transport.close()
        self.destroy()


if __name__ == "__main__":
    app = MatrixPaintApp()
    app.mainloop()
