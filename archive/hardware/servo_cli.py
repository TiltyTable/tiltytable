#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "servo_calibration.json"
DEFAULT_BAUDRATE = 115200
DEFAULT_RESET_DELAY = 2.0
DEFAULT_TIMEOUT = 1.0
STATE_NAMES = ("wall", "floor", "hole")


def default_states_us() -> Dict[str, Optional[int]]:
    return {state: None for state in STATE_NAMES}


def normalize_states_us(raw_states: object) -> Dict[str, Optional[int]]:
    states = default_states_us()
    if not isinstance(raw_states, dict):
        return states

    for state in STATE_NAMES:
        value = raw_states.get(state)
        states[state] = None if value is None else int(value)

    return states


def default_config() -> Dict[str, object]:
    channels = []
    for channel in range(4):
        channels.append(
            {
                "name": f"servo{channel}",
                "channel": channel,
                "min_us": 500,
                "max_us": 2400,
                "home_deg": 90,
                "invert": False,
                "states_us": default_states_us(),
            }
        )

    return {
        "baudrate": DEFAULT_BAUDRATE,
        "servo_count": len(channels),
        "channels": channels,
    }


def bool_from_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"cannot parse boolean value from {value!r}")


def normalize_profile(raw: Dict[str, object], index: int) -> Dict[str, object]:
    profile = {
        "name": str(raw.get("name", f"servo{index}")),
        "channel": int(raw.get("channel", index)),
        "min_us": int(raw.get("min_us", 500)),
        "max_us": int(raw.get("max_us", 2400)),
        "home_deg": float(raw.get("home_deg", 90)),
        "invert": bool_from_value(raw.get("invert", False)),
        "states_us": normalize_states_us(raw.get("states_us")),
    }
    validate_profile(profile)
    return profile


def normalize_config(raw: Dict[str, object]) -> Dict[str, object]:
    template = default_config()
    merged = deepcopy(template)

    if isinstance(raw, dict):
        merged["baudrate"] = int(raw.get("baudrate", merged["baudrate"]))
        merged["servo_count"] = int(raw.get("servo_count", merged["servo_count"]))
        if "port" in raw and raw["port"]:
            merged["port"] = str(raw["port"])

        raw_channels = raw.get("channels", template["channels"])
        if not isinstance(raw_channels, list):
            raise ValueError("config field 'channels' must be a list")
        merged["channels"] = [normalize_profile(channel, index) for index, channel in enumerate(raw_channels)]
        merged["servo_count"] = len(merged["channels"])

    sort_profiles(merged)
    return merged


def validate_profile(profile: Dict[str, object]) -> None:
    channel = int(profile["channel"])
    min_us = int(profile["min_us"])
    max_us = int(profile["max_us"])
    home_deg = float(profile["home_deg"])
    states_us = profile.get("states_us", default_states_us())

    if channel < 0 or channel > 15:
        raise ValueError("channel must be between 0 and 15")
    if min_us < 100 or min_us > 3000:
        raise ValueError("min_us must be between 100 and 3000")
    if max_us < 100 or max_us > 3000:
        raise ValueError("max_us must be between 100 and 3000")
    if min_us >= max_us:
        raise ValueError("min_us must be lower than max_us")
    if home_deg < 0 or home_deg > 180:
        raise ValueError("home_deg must be between 0 and 180")
    if not isinstance(states_us, dict):
        raise ValueError("states_us must be an object")

    for state in STATE_NAMES:
        pulse_us = states_us.get(state)
        if pulse_us is None:
            continue
        pulse_us = int(pulse_us)
        if pulse_us < 100 or pulse_us > 3000:
            raise ValueError(f"{state} state must be between 100 and 3000 microseconds")


def sort_profiles(config: Dict[str, object]) -> None:
    channels = config.get("channels", [])
    if isinstance(channels, list):
        channels.sort(key=lambda profile: int(profile["channel"]))


def load_config(path: Path) -> Dict[str, object]:
    if not path.exists():
        return default_config()
    with path.open("r", encoding="utf-8") as handle:
        return normalize_config(json.load(handle))


def save_config(path: Path, config: Dict[str, object]) -> None:
    sort_profiles(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def serial_modules():
    try:
        import serial  # type: ignore
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required. Install it with:\n"
            "python3 -m pip install -r hardware/requirements.txt"
        ) from exc

    return serial, list_ports


def available_ports() -> List[object]:
    _, list_ports = serial_modules()
    return list(list_ports.comports())


def describe_ports() -> str:
    ports = available_ports()
    if not ports:
        return "No serial ports found."

    lines = []
    for port in ports:
        description = getattr(port, "description", "").strip() or "(no description)"
        hwid = getattr(port, "hwid", "").strip()
        if hwid:
            lines.append(f"{port.device}  {description}  [{hwid}]")
        else:
            lines.append(f"{port.device}  {description}")
    return "\n".join(lines)


def resolve_port(requested: Optional[str], config: Dict[str, object]) -> str:
    if requested:
        return requested

    configured_port = config.get("port")
    if configured_port:
        return str(configured_port)

    ports = available_ports()
    if not ports:
        raise SystemExit("No serial ports found. Connect the Arduino and try again.")

    if len(ports) == 1:
        return str(ports[0].device)

    ranked: List[str] = []
    keywords = ("arduino", "usbmodem", "usbserial", "wchusbserial", "ttyacm", "ttyusb")
    for port in ports:
        blob = " ".join(
            [
                str(getattr(port, "device", "")),
                str(getattr(port, "description", "")),
                str(getattr(port, "manufacturer", "")),
            ]
        ).lower()
        if any(keyword in blob for keyword in keywords):
            ranked.append(str(port.device))

    ranked = sorted(set(ranked))
    if len(ranked) == 1:
        return ranked[0]

    raise SystemExit(
        "Multiple serial ports are available. Re-run with --port.\n\n"
        f"{describe_ports()}"
    )


def channel_name_map(config: Dict[str, object]) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for profile in config.get("channels", []):
        mapping[int(profile["channel"])] = str(profile["name"])
    return mapping


def resolve_profile(config: Dict[str, object], target: str) -> Dict[str, object]:
    profiles = config.get("channels", [])
    if not isinstance(profiles, list):
        raise ValueError("config channels are missing")

    lowered = target.strip().lower()

    for profile in profiles:
        if str(profile["name"]).lower() == lowered:
            return profile

    try:
        number = int(lowered)
    except ValueError as exc:
        raise ValueError(f"unknown servo target {target!r}") from exc

    for profile in profiles:
        if int(profile["channel"]) == number:
            return profile

    if 0 <= number < len(profiles):
        return profiles[number]

    raise ValueError(f"no configured servo matches {target!r}")


def status_by_channel(rows: Iterable[Dict[str, object]]) -> Dict[int, Dict[str, object]]:
    return {int(row["channel"]): row for row in rows}


def configured_profiles(config: Dict[str, object]) -> List[Dict[str, object]]:
    profiles = config.get("channels", [])
    if not isinstance(profiles, list):
        return []
    return sorted(profiles, key=lambda profile: int(profile["channel"]))


def normalize_state_name(state_name: str) -> str:
    normalized = state_name.strip().lower()
    if normalized not in STATE_NAMES:
        raise ValueError(f"state must be one of: {', '.join(STATE_NAMES)}")
    return normalized


def profile_states(profile: Dict[str, object]) -> Dict[str, Optional[int]]:
    states_us = profile.get("states_us")
    if not isinstance(states_us, dict):
        states_us = default_states_us()
        profile["states_us"] = states_us

    normalized = default_states_us()
    for state in STATE_NAMES:
        value = states_us.get(state)
        normalized[state] = None if value is None else int(value)

    profile["states_us"] = normalized
    return normalized


def clamp_to_profile_limits(profile: Dict[str, object], pulse_us: int) -> int:
    return max(int(profile["min_us"]), min(int(profile["max_us"]), int(pulse_us)))


def render_saved_states(config: Dict[str, object], target: Optional[str] = None) -> str:
    if target is None:
        profiles = configured_profiles(config)
    else:
        profiles = [resolve_profile(config, target)]

    headers = ["Ch", "Name", "WallUs", "FloorUs", "HoleUs"]
    rows: List[List[str]] = []
    for profile in profiles:
        states_us = profile_states(profile)
        rows.append(
            [
                str(int(profile["channel"])),
                str(profile["name"]),
                "-" if states_us["wall"] is None else str(int(states_us["wall"])),
                "-" if states_us["floor"] is None else str(int(states_us["floor"])),
                "-" if states_us["hole"] is None else str(int(states_us["hole"])),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def format_row(values: List[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    output = [format_row(headers), format_row(["-" * width for width in widths])]
    output.extend(format_row(row) for row in rows)
    return "\n".join(output)


def render_status(rows: List[Dict[str, object]], config: Dict[str, object], show_all: bool = False) -> str:
    names = channel_name_map(config)
    by_channel = status_by_channel(rows)

    if show_all:
        channels = sorted(by_channel.keys())
    else:
        channels = sorted(names.keys()) or sorted(by_channel.keys())

    table_rows: List[List[str]] = []
    for channel in channels:
        if channel not in by_channel:
            continue
        row = by_channel[channel]
        table_rows.append(
            [
                str(channel),
                names.get(channel, "-"),
                "on" if row["enabled"] else "off",
                str(row["min_us"]),
                str(row["max_us"]),
                f"{row['home_deg']:.1f}",
                "yes" if row["invert"] else "no",
                str(row["last_us"]),
            ]
        )

    headers = ["Ch", "Name", "En", "Min", "Max", "Home", "Inv", "LastUs"]
    widths = [len(header) for header in headers]
    for row in table_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def format_row(values: List[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    output = [format_row(headers), format_row(["-" * width for width in widths])]
    output.extend(format_row(row) for row in table_rows)
    return "\n".join(output)


class ServoBridge:
    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout: float = DEFAULT_TIMEOUT,
        reset_delay: float = DEFAULT_RESET_DELAY,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.reset_delay = reset_delay
        self._serial = None

    def open(self) -> None:
        serial, _ = serial_modules()
        self._serial = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )
        time.sleep(self.reset_delay)
        self._clear_buffers()
        self.ping()

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def __enter__(self) -> "ServoBridge":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _require_open(self):
        if self._serial is None:
            raise RuntimeError("serial port is not open")
        return self._serial

    def _clear_buffers(self) -> None:
        serial_handle = self._require_open()
        serial_handle.reset_input_buffer()
        serial_handle.reset_output_buffer()

    def _transact(self, command: str, completion_timeout: float = 5.0) -> Tuple[List[str], str]:
        serial_handle = self._require_open()
        serial_handle.write((command.strip() + "\n").encode("ascii"))
        serial_handle.flush()

        deadline = time.monotonic() + completion_timeout
        payload: List[str] = []
        while time.monotonic() < deadline:
            raw = serial_handle.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line or line.startswith("READY"):
                continue
            if line.startswith("OK"):
                return payload, line
            if line.startswith("ERR"):
                detail = line[4:].strip() if line.startswith("ERR ") else line
                raise RuntimeError(detail or "device returned an error")
            payload.append(line)

        raise TimeoutError(f"timed out waiting for device response to {command!r}")

    def ping(self) -> None:
        _, ok_line = self._transact("PING")
        if ok_line != "OK PONG":
            raise RuntimeError(f"unexpected ping response: {ok_line}")

    def request_status(self) -> List[Dict[str, object]]:
        lines, _ = self._transact("STATUS")
        rows: List[Dict[str, object]] = []
        inside_block = False
        for line in lines:
            if line == "STATUS_BEGIN":
                inside_block = True
                continue
            if line == "STATUS_END":
                inside_block = False
                continue
            if not inside_block:
                continue

            parts = line.split()
            if len(parts) != 8 or parts[0] != "STATUS":
                continue
            rows.append(
                {
                    "channel": int(parts[1]),
                    "enabled": bool(int(parts[2])),
                    "min_us": int(parts[3]),
                    "max_us": int(parts[4]),
                    "home_deg": float(parts[5]),
                    "invert": bool(int(parts[6])),
                    "last_us": int(parts[7]),
                }
            )

        return rows

    def move_angle(self, channel: int, angle: float) -> None:
        self._transact(f"MOVE {channel} {angle:.2f}")

    def set_pulse(self, channel: int, pulse_us: int) -> None:
        self._transact(f"PULSE {channel} {pulse_us}")

    def set_calibration(self, profile: Dict[str, object]) -> None:
        validate_profile(profile)
        invert_flag = 1 if bool(profile["invert"]) else 0
        self._transact(
            "CAL {channel} {min_us} {max_us} {home_deg:.2f} {invert}".format(
                channel=int(profile["channel"]),
                min_us=int(profile["min_us"]),
                max_us=int(profile["max_us"]),
                home_deg=float(profile["home_deg"]),
                invert=invert_flag,
            )
        )

    def apply_config(self, config: Dict[str, object]) -> None:
        for profile in configured_profiles(config):
            self.set_calibration(profile)

    def home(self, target: str) -> None:
        self._transact(f"HOME {target}")

    def enable(self, target: str) -> None:
        self._transact(f"ENABLE {target}")

    def disable(self, target: str) -> None:
        self._transact(f"DISABLE {target}")


def sweep_values(start: float, end: float, step: float) -> List[float]:
    if step <= 0:
        raise ValueError("step must be positive")

    direction = 1 if end >= start else -1
    step *= direction
    values = []
    current = start

    def done(value: float) -> bool:
        return value > end if direction > 0 else value < end

    while not done(current):
        values.append(round(current, 4))
        current += step

    if not values or values[-1] != end:
        values.append(end)

    return values


def interpolated_pulse_values(start_us: int, end_us: int, steps: int) -> List[int]:
    if steps < 1:
        raise ValueError("steps must be at least 1")

    if steps == 1:
        return [int(start_us), int(end_us)]

    values: List[int] = []
    for index in range(steps + 1):
        ratio = index / float(steps)
        pulse_us = round(start_us + ((end_us - start_us) * ratio))
        values.append(int(pulse_us))
    return values


REPL_HELP = """
Commands:
  status [all]                    Show device state.
  states [target]                 Show saved wall/floor/hole pulse values.
  angle <target> <deg>            Move one servo by angle.
  pulse <target> <microseconds>   Move one servo by raw pulse width.
  nudge <target> <delta_us>       Move one servo relative to its current pulse.
  sweep <target> <start> <end> <step> [delay_ms]
                                  Sweep a servo from start to end.
  cycle-all [cycles] [steps] [delay_ms] [hold_ms]
                                  Cycle all configured servos between min and max.
  state <target> <wall|floor|hole>
                                  Move one servo to a saved named state.
  capture <target> <wall|floor|hole>
                                  Save the current live pulse as a named state.
  set-state <target> <wall|floor|hole> <pulse_us>
                                  Save a named state directly by pulse width.
  home <target|all>               Move one servo or all configured servos to home.
  enable <target|all>             Re-enable pulse output.
  disable <target|all>            Turn pulse output off.
  cal <target> min|max|home <value>
                                  Update calibration for one servo and send it now.
  invert <target> on|off          Flip servo direction and send it now.
  name <target> <new_name>        Rename a configured servo locally.
  write                           Push all configured calibrations to the Arduino.
  save                            Save the local JSON calibration file.
  config                          Print the local config file contents.
  ports                           List serial ports.
  help                            Show this help.
  quit / exit                     Leave interactive mode.

Targets can be a channel number like 0 or a configured name like servo0.
""".strip()


def apply_to_all_configured(bridge: ServoBridge, config: Dict[str, object], verb: str) -> None:
    for profile in configured_profiles(config):
        channel = str(int(profile["channel"]))
        if verb == "home":
            bridge.home(channel)
        elif verb == "enable":
            bridge.enable(channel)
        elif verb == "disable":
            bridge.disable(channel)
        else:
            raise ValueError(f"unsupported verb {verb!r}")


def cycle_all_profiles(
    bridge: ServoBridge,
    config: Dict[str, object],
    cycles: int = 1,
    steps: int = 40,
    delay_s: float = 0.04,
    hold_s: float = 0.2,
) -> None:
    profiles = configured_profiles(config)
    if not profiles:
        raise ValueError("no configured servos to cycle")
    if cycles < 1:
        raise ValueError("cycles must be at least 1")
    if steps < 1:
        raise ValueError("steps must be at least 1")
    if delay_s < 0:
        raise ValueError("delay must be non-negative")
    if hold_s < 0:
        raise ValueError("hold time must be non-negative")

    ramps = []
    for profile in profiles:
        minimum = int(profile["min_us"])
        maximum = int(profile["max_us"])
        ramps.append(
            {
                "channel": int(profile["channel"]),
                "name": str(profile["name"]),
                "up": interpolated_pulse_values(minimum, maximum, steps),
                "down": interpolated_pulse_values(maximum, minimum, steps)[1:],
            }
        )

    for _ in range(cycles):
        for phase_name in ("up", "down"):
            phase_steps = ramps[0][phase_name]
            for index in range(len(phase_steps)):
                for ramp in ramps:
                    bridge.set_pulse(int(ramp["channel"]), int(ramp[phase_name][index]))
                if index < len(phase_steps) - 1:
                    time.sleep(delay_s)

            if hold_s > 0:
                time.sleep(hold_s)


def live_status_row(bridge: ServoBridge, profile: Dict[str, object]) -> Dict[str, object]:
    by_channel = status_by_channel(bridge.request_status())
    channel = int(profile["channel"])
    if channel not in by_channel:
        raise ValueError(f"channel {channel} is missing from the device status report")
    return by_channel[channel]


def move_profile_to_saved_state(
    bridge: ServoBridge,
    profile: Dict[str, object],
    state_name: str,
) -> Tuple[int, int]:
    normalized_state = normalize_state_name(state_name)
    states_us = profile_states(profile)
    saved_pulse_us = states_us[normalized_state]
    if saved_pulse_us is None:
        raise ValueError(
            f"{normalized_state} is not configured for channel {profile['channel']} ({profile['name']})"
        )

    effective_pulse_us = clamp_to_profile_limits(profile, int(saved_pulse_us))
    bridge.set_pulse(int(profile["channel"]), int(saved_pulse_us))
    return int(saved_pulse_us), effective_pulse_us


def capture_profile_state(
    bridge: ServoBridge,
    profile: Dict[str, object],
    state_name: str,
) -> int:
    normalized_state = normalize_state_name(state_name)
    row = live_status_row(bridge, profile)
    pulse_us = int(row["last_us"])
    states_us = profile_states(profile)
    states_us[normalized_state] = pulse_us
    validate_profile(profile)
    return pulse_us


def set_profile_state(profile: Dict[str, object], state_name: str, pulse_us: int) -> int:
    normalized_state = normalize_state_name(state_name)
    states_us = profile_states(profile)
    states_us[normalized_state] = int(pulse_us)
    validate_profile(profile)
    return int(pulse_us)


def nudge_profile(bridge: ServoBridge, profile: Dict[str, object], delta_us: int) -> Tuple[int, int]:
    row = live_status_row(bridge, profile)
    current_pulse_us = int(row["last_us"])
    target_pulse_us = clamp_to_profile_limits(profile, current_pulse_us + int(delta_us))
    bridge.set_pulse(int(profile["channel"]), target_pulse_us)
    return current_pulse_us, target_pulse_us


def interactive_session(bridge: ServoBridge, config: Dict[str, object], config_path: Path) -> int:
    print(f"Connected to {bridge.port} at {bridge.baudrate} baud.")
    print("Type 'help' for commands.")

    while True:
        try:
            raw = input("servo> ").strip()
        except EOFError:
            print()
            return 0

        if not raw:
            continue

        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            print(f"error: {exc}")
            continue

        command = parts[0].lower()
        try:
            if command in {"quit", "exit"}:
                return 0

            if command == "help":
                print(REPL_HELP)
                continue

            if command == "ports":
                print(describe_ports())
                continue

            if command == "config":
                print(json.dumps(config, indent=2))
                continue

            if command == "write":
                bridge.apply_config(config)
                print(f"Applied calibration to {len(config.get('channels', []))} configured servos.")
                continue

            if command == "save":
                save_config(config_path, config)
                print(f"Saved calibration to {config_path}")
                continue

            if command == "status":
                show_all = len(parts) > 1 and parts[1].lower() == "all"
                print(render_status(bridge.request_status(), config, show_all=show_all))
                continue

            if command == "states" and len(parts) in {1, 2}:
                target = parts[1] if len(parts) == 2 else None
                print(render_saved_states(config, target=target))
                continue

            if command == "angle" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                angle = float(parts[2])
                bridge.move_angle(int(profile["channel"]), angle)
                print(f"Moved channel {profile['channel']} ({profile['name']}) to {angle:.1f} deg.")
                continue

            if command == "pulse" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                pulse_us = int(parts[2])
                bridge.set_pulse(int(profile["channel"]), pulse_us)
                print(f"Moved channel {profile['channel']} ({profile['name']}) to {pulse_us} us.")
                continue

            if command == "nudge" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                current_pulse_us, target_pulse_us = nudge_profile(bridge, profile, int(parts[2]))
                print(
                    f"Nudged channel {profile['channel']} ({profile['name']}) "
                    f"from {current_pulse_us} us to {target_pulse_us} us."
                )
                continue

            if command == "sweep" and len(parts) in {5, 6}:
                profile = resolve_profile(config, parts[1])
                start = float(parts[2])
                end = float(parts[3])
                step = float(parts[4])
                delay_s = (float(parts[5]) / 1000.0) if len(parts) == 6 else 0.08
                values = sweep_values(start, end, step)
                for value in values:
                    bridge.move_angle(int(profile["channel"]), value)
                    time.sleep(delay_s)
                print(
                    f"Swept channel {profile['channel']} ({profile['name']}) "
                    f"from {start:.1f} to {end:.1f} deg."
                )
                continue

            if command == "cycle-all" and len(parts) in {1, 2, 3, 4, 5}:
                cycles = int(parts[1]) if len(parts) >= 2 else 1
                steps = int(parts[2]) if len(parts) >= 3 else 40
                delay_s = (float(parts[3]) / 1000.0) if len(parts) >= 4 else 0.04
                hold_s = (float(parts[4]) / 1000.0) if len(parts) >= 5 else 0.2
                cycle_all_profiles(bridge, config, cycles=cycles, steps=steps, delay_s=delay_s, hold_s=hold_s)
                print(
                    f"Cycled {len(configured_profiles(config))} configured servos "
                    f"between min and max for {cycles} cycle(s)."
                )
                continue

            if command == "state" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                saved_pulse_us, effective_pulse_us = move_profile_to_saved_state(bridge, profile, parts[2])
                print(
                    f"Moved channel {profile['channel']} ({profile['name']}) "
                    f"to {normalize_state_name(parts[2])} at {effective_pulse_us} us."
                )
                if saved_pulse_us != effective_pulse_us:
                    print(
                        f"note: saved value {saved_pulse_us} us was clamped to "
                        f"{effective_pulse_us} us by the current min/max limits."
                    )
                continue

            if command == "capture" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                pulse_us = capture_profile_state(bridge, profile, parts[2])
                print(
                    f"Captured {normalize_state_name(parts[2])} for channel {profile['channel']} "
                    f"({profile['name']}) at {pulse_us} us."
                )
                continue

            if command == "set-state" and len(parts) == 4:
                profile = resolve_profile(config, parts[1])
                pulse_us = set_profile_state(profile, parts[2], int(parts[3]))
                print(
                    f"Saved {normalize_state_name(parts[2])} for channel {profile['channel']} "
                    f"({profile['name']}) at {pulse_us} us."
                )
                continue

            if command == "home" and len(parts) == 2:
                target = parts[1]
                if target.lower() == "all":
                    apply_to_all_configured(bridge, config, "home")
                    print("Moved all configured servos to their home angles.")
                else:
                    profile = resolve_profile(config, target)
                    bridge.home(str(int(profile["channel"])))
                    print(f"Moved channel {profile['channel']} ({profile['name']}) home.")
                continue

            if command == "enable" and len(parts) == 2:
                target = parts[1]
                if target.lower() == "all":
                    apply_to_all_configured(bridge, config, "enable")
                    print("Enabled all PWM outputs.")
                else:
                    profile = resolve_profile(config, target)
                    bridge.enable(str(int(profile["channel"])))
                    print(f"Enabled channel {profile['channel']} ({profile['name']}).")
                continue

            if command == "disable" and len(parts) == 2:
                target = parts[1]
                if target.lower() == "all":
                    apply_to_all_configured(bridge, config, "disable")
                    print("Disabled all PWM outputs.")
                else:
                    profile = resolve_profile(config, target)
                    bridge.disable(str(int(profile["channel"])))
                    print(f"Disabled channel {profile['channel']} ({profile['name']}).")
                continue

            if command == "cal" and len(parts) == 4:
                profile = resolve_profile(config, parts[1])
                field = parts[2].lower()
                value = float(parts[3])
                if field == "min":
                    profile["min_us"] = int(value)
                elif field == "max":
                    profile["max_us"] = int(value)
                elif field == "home":
                    profile["home_deg"] = value
                else:
                    raise ValueError("cal field must be one of: min, max, home")
                validate_profile(profile)
                bridge.set_calibration(profile)
                print(
                    f"Updated {field} for channel {profile['channel']} "
                    f"({profile['name']}) and sent it to the device."
                )
                continue

            if command == "invert" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                profile["invert"] = bool_from_value(parts[2])
                bridge.set_calibration(profile)
                print(
                    f"Set invert={profile['invert']} for channel {profile['channel']} "
                    f"({profile['name']}) and sent it to the device."
                )
                continue

            if command == "name" and len(parts) == 3:
                profile = resolve_profile(config, parts[1])
                profile["name"] = parts[2]
                print(f"Renamed channel {profile['channel']} to {profile['name']}.")
                continue

            print(REPL_HELP)

        except Exception as exc:
            print(f"error: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate and manually drive servos through an Arduino -> PCA9685 bridge."
    )
    parser.add_argument("--port", help="Serial port for the Arduino, e.g. /dev/cu.usbmodem14101")
    parser.add_argument("--baud", type=int, help=f"Serial baud rate (default: {DEFAULT_BAUDRATE})")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Calibration file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--reset-delay",
        type=float,
        default=DEFAULT_RESET_DELAY,
        help=f"Seconds to wait after opening the port (default: {DEFAULT_RESET_DELAY})",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ports", help="List available serial ports")

    status_parser = subparsers.add_parser("status", help="Show live servo status")
    status_parser.add_argument("--all", action="store_true", help="Show all 16 PCA9685 channels")

    states_parser = subparsers.add_parser("states", help="Show saved wall/floor/hole pulse values")
    states_parser.add_argument("target", nargs="?", help="Optional channel number or configured servo name")

    apply_parser = subparsers.add_parser("apply-config", help="Send all configured calibration values")
    apply_parser.add_argument("--save", action="store_true", help="Save the config after applying it")

    angle_parser = subparsers.add_parser("angle", help="Move one servo by angle")
    angle_parser.add_argument("target", help="Channel number or configured servo name")
    angle_parser.add_argument("angle", type=float, help="Target angle in degrees")

    pulse_parser = subparsers.add_parser("pulse", help="Move one servo by pulse width")
    pulse_parser.add_argument("target", help="Channel number or configured servo name")
    pulse_parser.add_argument("pulse_us", type=int, help="Pulse width in microseconds")

    nudge_parser = subparsers.add_parser("nudge", help="Move one servo relative to its current pulse")
    nudge_parser.add_argument("target", help="Channel number or configured servo name")
    nudge_parser.add_argument("delta_us", type=int, help="Signed pulse delta in microseconds")

    cycle_parser = subparsers.add_parser(
        "cycle-all",
        help="Cycle all configured servos between min and max pulse limits",
    )
    cycle_parser.add_argument("cycles", nargs="?", type=int, default=1, help="Full min->max->min cycles")
    cycle_parser.add_argument("steps", nargs="?", type=int, default=40, help="Interpolation steps per half-cycle")
    cycle_parser.add_argument("delay_ms", nargs="?", type=float, default=40.0, help="Delay between steps in ms")
    cycle_parser.add_argument("hold_ms", nargs="?", type=float, default=200.0, help="Pause at each endpoint in ms")

    state_parser = subparsers.add_parser("state", help="Move one servo to a saved wall/floor/hole state")
    state_parser.add_argument("target", help="Channel number or configured servo name")
    state_parser.add_argument("state_name", help="One of: wall, floor, hole")

    capture_parser = subparsers.add_parser(
        "capture-state",
        help="Capture the current live pulse as a saved wall/floor/hole state",
    )
    capture_parser.add_argument("target", help="Channel number or configured servo name")
    capture_parser.add_argument("state_name", help="One of: wall, floor, hole")

    set_state_parser = subparsers.add_parser("set-state", help="Save a wall/floor/hole state directly")
    set_state_parser.add_argument("target", help="Channel number or configured servo name")
    set_state_parser.add_argument("state_name", help="One of: wall, floor, hole")
    set_state_parser.add_argument("pulse_us", type=int, help="Pulse width in microseconds")

    home_parser = subparsers.add_parser("home", help="Move one servo or all servos home")
    home_parser.add_argument("target", help="Channel number, servo name, or 'all'")

    enable_parser = subparsers.add_parser("enable", help="Enable one servo or all PWM outputs")
    enable_parser.add_argument("target", help="Channel number, servo name, or 'all'")

    disable_parser = subparsers.add_parser("disable", help="Disable one servo or all PWM outputs")
    disable_parser.add_argument("target", help="Channel number, servo name, or 'all'")

    interactive_parser = subparsers.add_parser("interactive", help="Open an interactive servo shell")
    interactive_parser.set_defaults(command="interactive")

    return parser


def open_bridge_from_args(args: argparse.Namespace, config: Dict[str, object]) -> ServoBridge:
    baudrate = int(args.baud or config.get("baudrate", DEFAULT_BAUDRATE))
    port = resolve_port(args.port, config)
    return ServoBridge(port=port, baudrate=baudrate, reset_delay=args.reset_delay)


def run_noninteractive(args: argparse.Namespace, config: Dict[str, object]) -> int:
    if args.command == "ports":
        print(describe_ports())
        return 0

    if args.command == "states":
        print(render_saved_states(config, target=args.target))
        return 0

    if args.command == "set-state":
        profile = resolve_profile(config, args.target)
        pulse_us = set_profile_state(profile, args.state_name, args.pulse_us)
        save_config(args.config, config)
        print(
            f"Saved {normalize_state_name(args.state_name)} for channel {profile['channel']} "
            f"({profile['name']}) at {pulse_us} us."
        )
        return 0

    with open_bridge_from_args(args, config) as bridge:
        if args.command == "status":
            print(render_status(bridge.request_status(), config, show_all=args.all))
            return 0

        if args.command == "apply-config":
            bridge.apply_config(config)
            if args.save:
                save_config(args.config, config)
            print(f"Applied calibration to {len(config.get('channels', []))} configured servos.")
            return 0

        if args.command == "angle":
            profile = resolve_profile(config, args.target)
            bridge.move_angle(int(profile["channel"]), args.angle)
            print(f"Moved channel {profile['channel']} ({profile['name']}) to {args.angle:.1f} deg.")
            return 0

        if args.command == "pulse":
            profile = resolve_profile(config, args.target)
            bridge.set_pulse(int(profile["channel"]), args.pulse_us)
            print(f"Moved channel {profile['channel']} ({profile['name']}) to {args.pulse_us} us.")
            return 0

        if args.command == "nudge":
            profile = resolve_profile(config, args.target)
            current_pulse_us, target_pulse_us = nudge_profile(bridge, profile, args.delta_us)
            print(
                f"Nudged channel {profile['channel']} ({profile['name']}) "
                f"from {current_pulse_us} us to {target_pulse_us} us."
            )
            return 0

        if args.command == "cycle-all":
            cycle_all_profiles(
                bridge,
                config,
                cycles=args.cycles,
                steps=args.steps,
                delay_s=(args.delay_ms / 1000.0),
                hold_s=(args.hold_ms / 1000.0),
            )
            print(
                f"Cycled {len(configured_profiles(config))} configured servos "
                f"between min and max for {args.cycles} cycle(s)."
            )
            return 0

        if args.command == "state":
            profile = resolve_profile(config, args.target)
            saved_pulse_us, effective_pulse_us = move_profile_to_saved_state(bridge, profile, args.state_name)
            print(
                f"Moved channel {profile['channel']} ({profile['name']}) "
                f"to {normalize_state_name(args.state_name)} at {effective_pulse_us} us."
            )
            if saved_pulse_us != effective_pulse_us:
                print(
                    f"note: saved value {saved_pulse_us} us was clamped to "
                    f"{effective_pulse_us} us by the current min/max limits."
                )
            return 0

        if args.command == "capture-state":
            profile = resolve_profile(config, args.target)
            pulse_us = capture_profile_state(bridge, profile, args.state_name)
            print(
                f"Captured {normalize_state_name(args.state_name)} for channel {profile['channel']} "
                f"({profile['name']}) at {pulse_us} us."
            )
            return 0

        if args.command == "home":
            if args.target.lower() == "all":
                apply_to_all_configured(bridge, config, "home")
                print("Moved all configured servos home.")
            else:
                profile = resolve_profile(config, args.target)
                bridge.home(str(int(profile["channel"])))
                print(f"Moved channel {profile['channel']} ({profile['name']}) home.")
            return 0

        if args.command == "enable":
            if args.target.lower() == "all":
                apply_to_all_configured(bridge, config, "enable")
                print("Enabled all PWM outputs.")
            else:
                profile = resolve_profile(config, args.target)
                bridge.enable(str(int(profile["channel"])))
                print(f"Enabled channel {profile['channel']} ({profile['name']}).")
            return 0

        if args.command == "disable":
            if args.target.lower() == "all":
                apply_to_all_configured(bridge, config, "disable")
                print("Disabled all PWM outputs.")
            else:
                profile = resolve_profile(config, args.target)
                bridge.disable(str(int(profile["channel"])))
                print(f"Disabled channel {profile['channel']} ({profile['name']}).")
            return 0

        if args.command in {None, "interactive"}:
            return interactive_session(bridge, config, args.config)

    raise SystemExit(f"unsupported command: {args.command!r}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    try:
        return run_noninteractive(args, config)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
