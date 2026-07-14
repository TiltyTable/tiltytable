#!/usr/bin/env python3
"""Button-driven LED colour calibration for the 3x3 module table.

Select a physical 4x4 module, cycle the five inspection colours, then use
the R/G/B +/- buttons to change the saved gain for every cell in that module.
Only LEDs are addressed; this tool never moves servos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:
    sys.exit("Tkinter is required for the button UI (install python3-tk).")

from led_color import (
    CAL_DIR, calibration_name, clear_color_override, get_color_override, ideal_rgb, load_cal, load_palette,
    resolve_name, save_cal, set_color_override,
)
from led_color_cal_tool import Link, autodetect_port, module_cells


GRID = 12
MODULE = 4
LED_CONFIG = os.path.join(CAL_DIR, "led_grid_config.json")
MIN_RGB = 0
MAX_RGB = 255

# Inspection colours already defined in calibration/led_palette.json.  These
# are palette IDs, rather than raw RGB, so the displayed values exercise the
# same path used by game_runner.py.
COLOURS = (
    "trap", "wall", "start", "end", "yellow", "floor", "points", "off",
)
COLOUR_LABELS = {
    "wall": "Green",
    "trap": "Red",
    "start": "Cyan",
    "end": "Magenta",
    "yellow": "Yellow",
    "floor": "Gray",
    "points": "Blue",
    "off": "Black / off",
}


class CalibrationApp:
    def __init__(self, root: tk.Tk, link: Link, led_cfg: dict, palette: dict, cal: dict):
        self.root = root
        self.link = link
        self.led_cfg = led_cfg
        self.palette = palette
        self.cal = cal
        self.selected = (0, 0)
        self.colour_index = 0
        self.dirty = False
        self.busy = False
        self.controls: list[tk.Widget] = []
        self.module_buttons: dict[tuple[int, int], tk.Button] = {}
        self.rgb_vars = {channel: tk.IntVar(value=0) for channel in ("r", "g", "b")}
        self.brightness_var = tk.IntVar(value=0)
        self.strip_counts = {
            int(strip): int(meta.get("led_count", 50))
            for strip, meta in led_cfg.get("strips", {}).items()
        }

        self.root.title("TiltyTable LED colour calibration")
        self.root.resizable(False, False)
        self._build_ui()
        self._update_labels()
        self._run_io(
            f"Preparing LED strands and showing {COLOUR_LABELS[self.colour]}…",
            self._initialise,
        )

    @property
    def colour(self) -> str:
        return COLOURS[self.colour_index]

    @property
    def calibration_colour(self) -> str:
        return calibration_name(self.palette, self.colour)

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, padx=14, pady=14)
        outer.grid(sticky="nsew")

        tk.Label(
            outer,
            text="Select a 4×4 module, then tune all 16 of its cells together.",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        modules = tk.LabelFrame(outer, text="Modules (table view)", padx=6, pady=6)
        modules.grid(row=1, column=0, columnspan=3, sticky="ew")
        for mr in range(3):
            for mc in range(3):
                button = tk.Button(
                    modules,
                    width=15,
                    height=2,
                    command=lambda r=mr, c=mc: self.select_module(r, c),
                )
                button.grid(row=mr, column=mc, padx=3, pady=3)
                self.module_buttons[(mr, mc)] = button
                self.controls.append(button)

        self.selection_label = tk.Label(outer, anchor="w")
        self.selection_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        self.gain_label = tk.Label(outer, anchor="w")
        self.gain_label.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        colour_frame = tk.LabelFrame(outer, text="Inspection colour", padx=6, pady=6)
        colour_frame.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.colour_label = tk.Label(colour_frame, width=20, font=("TkDefaultFont", 12, "bold"))
        self.colour_label.grid(row=0, column=0, padx=4)
        self.colour_use_label = tk.Label(colour_frame, anchor="w", fg="#555")
        self.colour_use_label.grid(row=1, column=0, padx=4, sticky="w")
        cycle = tk.Button(colour_frame, text="Next colour", command=self.cycle_colour)
        cycle.grid(row=0, column=1, rowspan=2, padx=4)
        self.controls.append(cycle)

        adjust = tk.LabelFrame(
            outer,
            text="Selected module, current colour: direct RGB output (release slider to apply)",
            padx=6,
            pady=6,
        )
        adjust.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        for row, (channel, colour_name) in enumerate((("r", "Red"), ("g", "Green"), ("b", "Blue"))):
            tk.Label(adjust, text=colour_name, width=8, anchor="w").grid(row=row, column=0, padx=4, pady=2)
            slider = tk.Scale(
                adjust,
                from_=MIN_RGB,
                to=MAX_RGB,
                resolution=1,
                orient=tk.HORIZONTAL,
                length=260,
                variable=self.rgb_vars[channel],
            )
            slider.grid(row=row, column=1, columnspan=2, padx=3, pady=2)
            slider.bind("<ButtonRelease-1>", lambda _event, ch=channel: self.set_channel_gain(ch))
            slider.bind("<KeyRelease>", lambda _event, ch=channel: self.set_channel_gain(ch))
            self.controls.append(slider)
        tk.Label(adjust, text="Brightness", width=8, anchor="w").grid(row=3, column=0, padx=4, pady=2)
        brightness = tk.Scale(
            adjust,
            from_=MIN_RGB,
            to=MAX_RGB,
            resolution=1,
            orient=tk.HORIZONTAL,
            length=260,
            variable=self.brightness_var,
        )
        brightness.grid(row=3, column=1, columnspan=2, padx=3, pady=2)
        brightness.bind("<ButtonRelease-1>", lambda _event: self.set_brightness())
        brightness.bind("<KeyRelease>", lambda _event: self.set_brightness())
        self.controls.append(brightness)
        tk.Label(
            adjust,
            text="RGB sliders save exact 0–255 output values for this module and selected colour.",
            fg="#555",
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=4, pady=(4, 0))
        reset_colour = tk.Button(
            adjust,
            text="Reset selected color to palette default",
            command=self.reset_selected_colour,
        )
        reset_colour.grid(row=5, column=0, columnspan=3, pady=(6, 0))
        self.controls.append(reset_colour)

        actions = tk.Frame(outer)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        save = tk.Button(actions, text="Save calibration", command=self.save)
        off = tk.Button(actions, text="All LEDs off", command=self.all_off)
        save.grid(row=0, column=0, padx=(0, 6))
        off.grid(row=0, column=1)
        self.controls.extend((save, off))

        self.status = tk.Label(outer, anchor="w", fg="#555")
        self.status.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def module_metadata(self, mr: int, mc: int) -> tuple[str, int]:
        row, col = mr * MODULE, mc * MODULE
        cell = self.led_cfg["cells"].get(f"{row},{col}", {})
        strip = int(cell.get("strip", -1))
        meta = self.led_cfg.get("strips", {}).get(str(strip), {})
        return str(meta.get("name", f"strip {strip}")), strip

    def _tile_rgb(self, row: int, col: int) -> tuple[int, int, int]:
        return (
            get_color_override(self.cal, self.calibration_colour, row, col)
            or ideal_rgb(self.palette, self.colour)
        )

    def _module_average_rgb(self, mr: int, mc: int) -> tuple[int, int, int]:
        cells = module_cells(mr, mc)
        values = [self._tile_rgb(r, c) for r, c in cells]
        return tuple(round(sum(v[i] for v in values) / len(values)) for i in range(3))  # type: ignore[return-value]

    def _update_labels(self) -> None:
        mr, mc = self.selected
        name, strip = self.module_metadata(mr, mc)
        self.selection_label.configure(text=f"Selected: module {mr},{mc} — {name}, LED strip {strip}")
        r, g, b = self._module_average_rgb(mr, mc)
        brightness = max(r, g, b)
        self.gain_label.configure(
            text=f"Current {COLOUR_LABELS[self.colour]} RGB:  R {r}    G {g}    B {b}    brightness {brightness}"
        )
        self.rgb_vars["r"].set(r)
        self.rgb_vars["g"].set(g)
        self.rgb_vars["b"].set(b)
        self.brightness_var.set(brightness)
        self.colour_label.configure(text=COLOUR_LABELS[self.colour])
        purpose = self.palette["colors"][self.colour].get("label", self.colour)
        self.colour_use_label.configure(text=f"Used for: {purpose}")
        for key, button in self.module_buttons.items():
            module_name, strip_index = self.module_metadata(*key)
            button.configure(text=f"{key[0]},{key[1]}  {module_name}\nstrip {strip_index}")
            button.configure(relief=tk.SUNKEN if key == self.selected else tk.RAISED)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        for control in self.controls:
            control.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.status.configure(text=status)

    def _run_io(self, status: str, operation) -> None:
        if self.busy:
            return
        self._set_busy(True, status)

        def worker() -> None:
            try:
                operation()
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda m=message: messagebox.showerror("LED calibration", m))
                done = f"Failed: {message}"
            else:
                done = "Ready"
            self.root.after(0, lambda result=done: self._set_busy(False, result))

        threading.Thread(target=worker, daemon=True).start()

    def _initialise(self) -> None:
        for strip, count in self.strip_counts.items():
            self.link.send(f"LN {strip} {count}")
            time.sleep(0.05)
        self._render([(r, c) for r in range(GRID) for c in range(GRID)])

    def _led_at(self, row: int, col: int):
        cell = self.led_cfg.get("cells", {}).get(f"{row},{col}")
        if not cell:
            return None
        return int(cell["strip"]), int(cell["index"])

    def _render(self, cells) -> None:
        for row, col in cells:
            loc = self._led_at(row, col)
            if loc is None:
                continue
            strip, index = loc
            r, g, b = resolve_name(self.palette, self.cal, self.colour, row, col)
            self.link.send(f"LP {strip} {index} {r} {g} {b}")
            time.sleep(max(0.015, self.strip_counts.get(strip, 50) * 0.0002))

    def select_module(self, mr: int, mc: int) -> None:
        if self.busy:
            return
        self.selected = (mr, mc)
        self._update_labels()

    def cycle_colour(self) -> None:
        if self.busy:
            return
        self.colour_index = (self.colour_index + 1) % len(COLOURS)
        self._update_labels()
        self._run_io(f"Showing {COLOUR_LABELS[self.colour]} across the table…", lambda: self._render(
            [(r, c) for r in range(GRID) for c in range(GRID)]
        ))

    def set_channel_gain(self, channel: str) -> None:
        if self.busy:
            return
        mr, mc = self.selected
        target = max(MIN_RGB, min(MAX_RGB, self.rgb_vars[channel].get()))
        for row, col in module_cells(mr, mc):
            r, g, b = self._tile_rgb(row, col)
            values = {"r": r, "g": g, "b": b}
            values[channel] = target
            set_color_override(
                self.cal, self.calibration_colour, row, col,
                (values["r"], values["g"], values["b"]),
            )
        self.dirty = True
        self._update_labels()
        self._run_io(
            f"Set {COLOUR_LABELS[self.colour]} {channel.upper()} to {target} on module {mr},{mc}; re-lighting it…",
            lambda: self._render(module_cells(mr, mc)),
        )

    def set_brightness(self) -> None:
        """Set the selected colour's maximum RGB component on all 16 tiles."""
        if self.busy:
            return
        mr, mc = self.selected
        target = max(MIN_RGB, min(MAX_RGB, self.brightness_var.get()))
        for row, col in module_cells(mr, mc):
            r, g, b = self._tile_rgb(row, col)
            current_max = max(r, g, b)
            if current_max:
                rgb = tuple(round(value * target / current_max) for value in (r, g, b))
            else:
                rgb = (0, 0, 0)
            set_color_override(self.cal, self.calibration_colour, row, col, rgb)
        self.dirty = True
        self._update_labels()
        self._run_io(
            f"Set {COLOUR_LABELS[self.colour]} brightness to {target} on module {mr},{mc}; re-lighting it…",
            lambda: self._render(module_cells(mr, mc)),
        )

    def reset_selected_colour(self) -> None:
        """Remove direct overrides so this module uses its defined palette RGB."""
        if self.busy:
            return
        mr, mc = self.selected
        for row, col in module_cells(mr, mc):
            clear_color_override(self.cal, self.calibration_colour, row, col)
        self.dirty = True
        self._update_labels()
        default = ideal_rgb(self.palette, self.colour)
        self._run_io(
            f"Reset {COLOUR_LABELS[self.colour]} on module {mr},{mc} to palette RGB {default}…",
            lambda: self._render(module_cells(mr, mc)),
        )

    def save(self) -> None:
        save_cal(self.cal)
        self.dirty = False
        self.status.configure(text="Saved calibration/led_color_cal.json")

    def all_off(self) -> None:
        self._run_io("Turning all LEDs off…", lambda: self.link.send("LX"))

    def close(self) -> None:
        if self.busy:
            messagebox.showinfo("LED calibration", "Wait for the current LED update to finish.")
            return
        if self.dirty and messagebox.askyesno("Save calibration?", "Save the RGB gain changes before closing?"):
            self.save()
        self.link.close()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()
    port = args.port or autodetect_port()
    if not port:
        sys.exit("No serial port; pass --port /dev/arduino-modules")

    with open(LED_CONFIG) as f:
        led_cfg = json.load(f)
    try:
        link = Link(port, args.baud)
        link.open_wait()
    except Exception as exc:
        sys.exit(f"Could not open {port}: {exc}")

    root = tk.Tk()
    CalibrationApp(root, link, led_cfg, load_palette(), load_cal())
    root.mainloop()


if __name__ == "__main__":
    main()
