from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

from game_runner import (
    BOARD_ORDER,
    Link,
    Table,
    cell_key_to_row_col,
    load_table_configs,
    normalize_addr,
    parse_map,
    tick_dynamic_cells,
)

START_COLOR = "#00E5FF"
END_COLOR = "#FF00AA"
DEFAULT_ARCADE_CONFIG = Path(__file__).with_name("config.json")


def load_module_start_delay_ms(
    config_path: Path = DEFAULT_ARCADE_CONFIG,
) -> int:
    with Path(config_path).open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    value = config.get("modules", {}).get("start_delay_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("arcade modules.start_delay_ms must be a non-negative integer")
    return value


class HardwareError(RuntimeError):
    pass


class BaseTableHardware:
    def initialize(self) -> None:
        raise NotImplementedError

    def load_level(self, map_path: Path, start_cell: str, end_cell: str) -> None:
        raise NotImplementedError

    def begin_play(self) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def cancel_load(self) -> None:
        raise NotImplementedError

    def apply_cell_updates(self, updates: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError

    def snapshot(self) -> dict[str, Any]:
        raise NotImplementedError


class SimulatedTableHardware(BaseTableHardware):
    def __init__(self) -> None:
        self.ready = False
        self.busy = False
        self.error = ""
        self.playing = False
        self.level = ""

    def initialize(self) -> None:
        self.ready = True
        self.error = ""

    def load_level(self, map_path: Path, start_cell: str, end_cell: str) -> None:
        self.level = map_path.name
        self.busy = False
        self.playing = False

    def begin_play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def cancel_load(self) -> None:
        self.busy = False
        self.playing = False

    def apply_cell_updates(self, updates: list[dict[str, Any]]) -> None:
        return

    def shutdown(self) -> None:
        self.ready = False
        self.playing = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": "simulation",
            "ready": self.ready,
            "busy": self.busy,
            "playing": self.playing,
            "error": self.error,
            "level": self.level,
        }


class ModuleGridHardware(BaseTableHardware):
    """Long-lived, exclusive module-grid owner.

    Level loads run in a worker because applying 144 LEDs/servos takes several
    seconds. All board selection + command sequences are serialized by
    ``_io_lock`` so blink/dynamic activity cannot interleave I2C board changes.
    """

    def __init__(
        self,
        port: str = "/dev/arduino-modules",
        baud: int = 115200,
        dry_run: bool = False,
        arcade_config_path: Path = DEFAULT_ARCADE_CONFIG,
    ) -> None:
        self.port = port
        self.baud = baud
        self.dry_run = dry_run
        self.module_start_delay_s = (
            load_module_start_delay_ms(arcade_config_path) / 1000.0
        )
        self.link: Link | None = None
        self.table: Table | None = None
        self.ready = False
        self.busy = False
        self.playing = False
        self.error = ""
        self.level = ""

        self._state_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._stop = threading.Event()
        self._animation_stop = threading.Event()
        self._generation = 0
        self._blink_entries: list[dict[str, Any]] = []
        self._dynamic: list[dict[str, Any]] = []
        self._play_started_at: float | None = None

    def initialize(self) -> None:
        with self._state_lock:
            if self.ready:
                self.error = ""
                return
            self.busy = True
            self.error = ""
        try:
            led_cfg, servo_grid_cfg, servo_configs = load_table_configs()
            self._validate_calibration(led_cfg, servo_grid_cfg, servo_configs)
            normalized_cells = {}
            for key, value in servo_grid_cfg.get("cells", {}).items():
                if not value:
                    continue
                normalized_cells[key] = {
                    "address": normalize_addr(value["address"]),
                    "channel": value["channel"],
                }
            servo_grid_cfg = {**servo_grid_cfg, "cells": normalized_cells}
            servo_configs = {
                normalize_addr(address): config for address, config in servo_configs.items()
            }

            link = Link(self.port, self.baud, dry_run=self.dry_run)
            link.open_wait()
            table = Table(
                link,
                led_cfg,
                servo_grid_cfg,
                servo_configs,
                module_start_delay_s=self.module_start_delay_s,
            )
            with self._io_lock:
                table.apply_led_counts()
                table.all_off()
            self.link = link
            self.table = table
            with self._state_lock:
                self.ready = True
        except Exception as exc:
            self.error = str(exc)
            if self.link:
                self.link.close()
            self.link = None
            self.table = None
            raise HardwareError(str(exc)) from exc
        finally:
            with self._state_lock:
                self.busy = False

    @staticmethod
    def _validate_calibration(
        led_cfg: dict[str, Any],
        servo_grid_cfg: dict[str, Any],
        servo_configs: dict[str, Any],
    ) -> None:
        expected = {f"{row},{col}" for row in range(12) for col in range(12)}
        led_cells = set(led_cfg.get("cells", {}))
        servo_cells = set(servo_grid_cfg.get("cells", {}))
        problems: list[str] = []
        if led_cells != expected:
            problems.append(f"LED map covers {len(led_cells)}/144 cells")
        if servo_cells != expected:
            problems.append(f"servo map covers {len(servo_cells)}/144 cells")
        if len(led_cfg.get("strips", {})) != 9:
            problems.append(f"LED config defines {len(led_cfg.get('strips', {}))}/9 strands")

        missing_positions = []
        for key, location in servo_grid_cfg.get("cells", {}).items():
            if not location:
                missing_positions.append(f"{key}: no location")
                continue
            address = normalize_addr(location["address"])
            channel = str(location["channel"])
            servo = servo_configs.get(address, {}).get("servos", {}).get(channel, {})
            missing = {"recessed", "neutral", "extended"} - set(servo)
            if missing:
                missing_positions.append(
                    f"{key} {address}/ch{channel}: {','.join(sorted(missing))}"
                )
        if missing_positions:
            problems.append(
                f"{len(missing_positions)} servo envelopes incomplete "
                f"({'; '.join(missing_positions[:3])})"
            )
        if problems:
            raise HardwareError("; ".join(problems))

    def load_level(self, map_path: Path, start_cell: str, end_cell: str) -> None:
        if not self.ready or not self.table:
            raise HardwareError("module grid is not initialized")
        self._stop_animations()
        with self._state_lock:
            if self.busy:
                raise HardwareError("module grid is already loading")
            self.busy = True
            self.playing = False
            self.error = ""
            self.level = map_path.name
            self._generation += 1
            generation = self._generation
        threading.Thread(
            target=self._load_worker,
            args=(Path(map_path), start_cell, end_cell, generation),
            name="arcade-level-loader",
            daemon=True,
        ).start()

    def _load_worker(
        self, map_path: Path, start_cell: str, end_cell: str, generation: int
    ) -> None:
        assert self.table is not None
        try:
            raw = json.loads(map_path.read_text(encoding="utf-8"))
            raw = copy.deepcopy(raw)
            raw[start_cell]["color"] = START_COLOR
            raw[end_cell]["color"] = END_COLOR
            static, dynamic = parse_map(raw)
            blink_entries = [self._entry(start_cell, value=-1, color=START_COLOR)]
            for key, cell in raw.items():
                if not isinstance(cell, dict) or not cell.get("blinkUntilPlay"):
                    continue
                if key == start_cell:
                    continue
                blink_entries.append(
                    self._entry(key, value=-1, color=str(cell.get("color", "#001FFF")))
                )
            blink_keys = {entry["key"] for entry in blink_entries}
            initial = [
                cell for cell in (static + dynamic) if cell["key"] not in blink_keys
            ] + blink_entries
            if generation != self._generation:
                return
            with self._io_lock:
                self.table.apply_cells(initial)
            if generation != self._generation:
                return
            self._blink_entries = blink_entries
            self._dynamic = dynamic
            self._play_started_at = None
            self._start_blink(generation)
        except Exception as exc:
            with self._state_lock:
                self.error = str(exc)
        finally:
            with self._state_lock:
                if generation == self._generation:
                    self.busy = False

    def _entry(self, key: str, value: int, color: str) -> dict[str, Any]:
        row, col = cell_key_to_row_col(key)
        return {
            "key": key,
            "row": row,
            "col": col,
            "value": value,
            "color": color,
            "rgb": (0, 0, 0),
        }

    def _start_blink(self, generation: int) -> None:
        self._animation_stop.clear()

        def blink() -> None:
            assert self.table is not None
            on = True
            while (
                not self._stop.is_set()
                and not self._animation_stop.wait(0.42)
                and generation == self._generation
                and not self.playing
            ):
                with self._io_lock:
                    for entry in self._blink_entries:
                        rgb = self.table.cell_led_rgb(entry) if on else (0, 0, 0)
                        self.table.set_led(entry["row"], entry["col"], rgb)
                on = not on

        threading.Thread(target=blink, name="arcade-start-blink", daemon=True).start()

    def begin_play(self) -> None:
        if not self.table or not self._blink_entries:
            raise HardwareError("level is not prepared")
        self._animation_stop.set()
        neutral_entries = [{**entry, "value": 0} for entry in self._blink_entries]
        with self._io_lock:
            self.table.apply_cells(neutral_entries)
        with self._state_lock:
            self.playing = True
        self._play_started_at = time.monotonic()
        self._start_dynamic_loop(self._generation)

    def _start_dynamic_loop(self, generation: int) -> None:
        if not self._dynamic:
            return
        now = time.monotonic()
        play_started = self._play_started_at or now
        dynamic = copy.deepcopy(self._dynamic)
        for cell in dynamic:
            if cell.get("dyn_type", "cycle") == "cycle":
                cell["next_t"] = now + cell["interval_s"]

        def animate() -> None:
            assert self.table is not None
            while (
                not self._stop.is_set()
                and generation == self._generation
                and self.playing
            ):
                now_t = time.monotonic()
                updates = tick_dynamic_cells(
                    dynamic,
                    now_t,
                    play_started=play_started,
                )
                if updates:
                    with self._io_lock:
                        self.table.apply_cells(updates)
                time.sleep(0.05)

        threading.Thread(target=animate, name="arcade-dynamic-tiles", daemon=True).start()

    def pause(self) -> None:
        with self._state_lock:
            self.playing = False
        self._animation_stop.set()
        self._release_servos()

    def cancel_load(self) -> None:
        """Abort an in-flight level load without disturbing an active play session."""
        with self._state_lock:
            if not self.busy:
                return
            self._generation += 1
            self.playing = False
            self.busy = False
            self._blink_entries = []
            self._dynamic = []
            self._play_started_at = None
        self._animation_stop.set()
        self._release_servos()

    def apply_cell_updates(self, updates: list[dict[str, Any]]) -> None:
        if not updates or not self.table:
            return
        with self._state_lock:
            if not self.playing:
                return
        with self._io_lock:
            self.table.apply_cells(updates)

    def _release_servos(self) -> None:
        if not self.link:
            return
        with self._io_lock:
            for address in BOARD_ORDER:
                self.link.select_board(address)
                self.link.send("X")

    def _stop_animations(self) -> None:
        with self._state_lock:
            self.playing = False
            self._generation += 1
        self._animation_stop.set()
        time.sleep(0.06)

    def shutdown(self) -> None:
        self._stop.set()
        self._stop_animations()
        if self.table:
            try:
                with self._io_lock:
                    self.table.all_off()
            except Exception:
                pass
        if self.link:
            self.link.close()
        with self._state_lock:
            self.ready = False
            self.busy = False
            self.playing = False

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "mode": "dry-run" if self.dry_run else "hardware",
                "ready": self.ready,
                "busy": self.busy,
                "playing": self.playing,
                "error": self.error,
                "level": self.level,
                "port": self.port,
            }
