from __future__ import annotations

import argparse
import copy
import json
import platform
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from flask import Flask, Response, jsonify, request, send_from_directory

if __package__ in {None, ""}:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - optional dependency on dev machines
    cv2 = None

from hardware.servo_cli import (
    DEFAULT_CONFIG_PATH,
    STATE_NAMES,
    ServoBridge,
    apply_to_all_configured,
    available_ports,
    capture_profile_state,
    configured_profiles,
    cycle_all_profiles,
    load_config,
    move_profile_to_saved_state,
    nudge_profile,
    profile_states,
    resolve_port,
    resolve_profile,
    save_config,
    set_profile_state,
    validate_profile,
)


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
DEFAULT_RUNTIME_STATE_PATH = ROOT_DIR / "runtime_state.json"
AUTO_CONNECT_COOLDOWN_S = 5.0


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def is_blank(value: Any) -> bool:
    return value is None or value == ""


def is_auto_blank(value: Any) -> bool:
    return is_blank(value) or value == "auto"


def server_host_metadata() -> dict[str, Any]:
    model = None
    model_path = Path("/proc/device-tree/model")
    if model_path.exists():
        try:
            model = model_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "").strip()
        except Exception:
            model = None

    hostname = socket.gethostname()
    return {
        "hostname": hostname,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "is_linux": sys.platform.startswith("linux"),
        "is_raspberry_pi": bool(model and "raspberry pi" in model.lower()),
        "model": model,
    }


def default_runtime_state() -> dict[str, Any]:
    return {
        "serial_port": None,
        "camera": {
            "device": None,
            "width": 1280,
            "height": 720,
            "fps": 30,
            "jpeg_quality": 82,
        },
    }


def normalize_runtime_state(raw: Any) -> dict[str, Any]:
    normalized = default_runtime_state()
    if not isinstance(raw, dict):
        return normalized

    serial_port = raw.get("serial_port")
    normalized["serial_port"] = None if is_blank(serial_port) else str(serial_port)

    camera_raw = raw.get("camera")
    if isinstance(camera_raw, dict):
        device = camera_raw.get("device")
        normalized["camera"]["device"] = None if is_blank(device) else str(device)

        if not is_blank(camera_raw.get("width")):
            normalized["camera"]["width"] = clamp_int(camera_raw["width"], 160, 3840)
        if not is_blank(camera_raw.get("height")):
            normalized["camera"]["height"] = clamp_int(camera_raw["height"], 120, 2160)
        if not is_blank(camera_raw.get("fps")):
            normalized["camera"]["fps"] = clamp_int(camera_raw["fps"], 1, 60)
        if not is_blank(camera_raw.get("jpeg_quality")):
            normalized["camera"]["jpeg_quality"] = clamp_int(camera_raw["jpeg_quality"], 40, 95)

    return normalized


class RuntimeStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_runtime_state()
        with self.path.open("r", encoding="utf-8") as handle:
            return normalize_runtime_state(json.load(handle))

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(self._state, handle, indent=2)
            handle.write("\n")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def set_serial_port(self, port: Optional[str]) -> None:
        with self._lock:
            self._state["serial_port"] = None if is_blank(port) else str(port)
            self._save_locked()

    def update_camera_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            camera = self._state.setdefault("camera", default_runtime_state()["camera"])
            if "device" in settings:
                device = settings["device"]
                camera["device"] = None if is_auto_blank(device) else str(device)
            if "width" in settings and not is_blank(settings["width"]):
                camera["width"] = clamp_int(settings["width"], 160, 3840)
            if "height" in settings and not is_blank(settings["height"]):
                camera["height"] = clamp_int(settings["height"], 120, 2160)
            if "fps" in settings and not is_blank(settings["fps"]):
                camera["fps"] = clamp_int(settings["fps"], 1, 60)
            if "jpeg_quality" in settings and not is_blank(settings["jpeg_quality"]):
                camera["jpeg_quality"] = clamp_int(settings["jpeg_quality"], 40, 95)
            self._save_locked()
            return copy.deepcopy(camera)


class EventLog:
    def __init__(self, max_entries: int = 250) -> None:
        self._lock = threading.Lock()
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._next_id = 1

    def add(
        self,
        level: str,
        source: str,
        message: str,
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self._lock:
            entry = {
                "id": self._next_id,
                "timestamp": utc_timestamp(),
                "level": level,
                "source": source,
                "message": message,
            }
            if data:
                entry["data"] = data
            self._next_id += 1
            self._entries.appendleft(entry)
            return entry

    def info(self, source: str, message: str, data: Optional[dict[str, Any]] = None) -> None:
        self.add("info", source, message, data)

    def warn(self, source: str, message: str, data: Optional[dict[str, Any]] = None) -> None:
        self.add("warn", source, message, data)

    def error(self, source: str, message: str, data: Optional[dict[str, Any]] = None) -> None:
        self.add("error", source, message, data)

    def tail(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)[:limit]


def pulse_to_angle(profile: dict[str, Any], pulse_us: int) -> float:
    minimum = int(profile["min_us"])
    maximum = int(profile["max_us"])
    if maximum <= minimum:
        return float(profile["home_deg"])
    clamped = max(minimum, min(maximum, int(pulse_us)))
    ratio = (clamped - minimum) / float(maximum - minimum)
    if bool(profile["invert"]):
        ratio = 1.0 - ratio
    return round(ratio * 180.0, 1)


def serialize_port(port_info: Any) -> dict[str, Any]:
    return {
        "device": str(getattr(port_info, "device", "")),
        "description": str(getattr(port_info, "description", "") or "(no description)"),
        "manufacturer": str(getattr(port_info, "manufacturer", "") or ""),
        "hwid": str(getattr(port_info, "hwid", "") or ""),
    }


def serialize_profile(profile: dict[str, Any], live_row: Optional[dict[str, Any]]) -> dict[str, Any]:
    states_us = profile_states(profile)
    payload = {
        "name": str(profile["name"]),
        "channel": int(profile["channel"]),
        "min_us": int(profile["min_us"]),
        "max_us": int(profile["max_us"]),
        "home_deg": float(profile["home_deg"]),
        "invert": bool(profile["invert"]),
        "states_us": {state: states_us[state] for state in STATE_NAMES},
        "saved_state_count": sum(1 for state in STATE_NAMES if states_us[state] is not None),
        "live": None,
    }

    if live_row:
        payload["live"] = {
            "enabled": bool(live_row["enabled"]),
            "min_us": int(live_row["min_us"]),
            "max_us": int(live_row["max_us"]),
            "home_deg": float(live_row["home_deg"]),
            "invert": bool(live_row["invert"]),
            "last_us": int(live_row["last_us"]),
            "last_angle_deg": pulse_to_angle(profile, int(live_row["last_us"])),
        }

    return payload


class ServoService:
    def __init__(self, config_path: Path, runtime_state: RuntimeStateStore, events: EventLog) -> None:
        self.config_path = config_path
        self.runtime_state = runtime_state
        self.events = events
        self._lock = threading.RLock()
        self._config = load_config(config_path)
        self._bridge: Optional[ServoBridge] = None
        self._connected_port: Optional[str] = None
        self._last_status_rows: list[dict[str, Any]] = []
        self._last_status_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_auto_connect_attempt = 0.0

    def _reload_config_locked(self) -> None:
        self._config = load_config(self.config_path)

    def _close_bridge_locked(self, log_message: Optional[str] = None) -> None:
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception:
                pass
        self._bridge = None
        self._connected_port = None
        if log_message:
            self.events.info("servo", log_message)

    def _list_ports_locked(self) -> tuple[list[dict[str, Any]], Optional[str]]:
        try:
            return [serialize_port(port) for port in available_ports()], None
        except SystemExit as exc:
            return [], str(exc)
        except Exception as exc:
            return [], str(exc)

    def _refresh_status_locked(self) -> list[dict[str, Any]]:
        if self._bridge is None:
            return []
        try:
            rows = self._bridge.request_status()
        except Exception as exc:
            self._last_error = f"Status refresh failed: {exc}"
            self.events.error("servo", self._last_error)
            self._close_bridge_locked()
            return []
        self._last_status_rows = rows
        self._last_status_at = utc_timestamp()
        self._last_error = None
        return rows

    def _ensure_bridge_locked(self) -> ServoBridge:
        if self._bridge is None:
            self.connect()
        assert self._bridge is not None
        return self._bridge

    def _with_bridge_locked(self, description: str, callback: Callable[[ServoBridge], Any]) -> Any:
        bridge = self._ensure_bridge_locked()
        try:
            result = callback(bridge)
            self._last_error = None
        except Exception as exc:
            message = f"{description} failed: {exc}"
            self._last_error = message
            self.events.error("servo", message)
            self._close_bridge_locked()
            raise RuntimeError(message) from exc

        try:
            self._refresh_status_locked()
        except Exception:
            pass

        return result

    def _maybe_auto_connect_locked(self) -> None:
        if self._bridge is not None:
            return
        now = time.monotonic()
        if now - self._last_auto_connect_attempt < AUTO_CONNECT_COOLDOWN_S:
            return
        self._last_auto_connect_attempt = now
        try:
            self.connect(auto=True)
        except RuntimeError:
            return

    def _candidate_ports_locked(self, requested_port: Optional[str], runtime_port: Optional[str]) -> list[str]:
        if requested_port:
            return [str(requested_port)]

        candidates: list[str] = []
        for source_port in (runtime_port, self._config.get("port")):
            if is_blank(source_port):
                continue
            source_port = str(source_port)
            if source_port not in candidates:
                candidates.append(source_port)

        auto_config = copy.deepcopy(self._config)
        auto_config.pop("port", None)
        try:
            auto_port = resolve_port(None, auto_config)
        except SystemExit:
            auto_port = None

        if auto_port and auto_port not in candidates:
            candidates.append(str(auto_port))

        return candidates

    def connect(self, requested_port: Optional[str] = None, auto: bool = False) -> None:
        with self._lock:
            self._reload_config_locked()
            runtime_snapshot = self.runtime_state.snapshot()
            runtime_port = runtime_snapshot.get("serial_port")
            candidate_ports = self._candidate_ports_locked(requested_port, runtime_port)
            if not candidate_ports:
                try:
                    candidate_ports = [resolve_port(requested_port, self._config)]
                except SystemExit as exc:
                    self._last_error = str(exc)
                    if not auto:
                        self.events.error("servo", self._last_error)
                    raise RuntimeError(self._last_error) from exc

            if self._bridge is not None and self._connected_port in candidate_ports:
                try:
                    self._bridge.ping()
                    self._last_error = None
                    self._refresh_status_locked()
                    return
                except Exception:
                    self._close_bridge_locked()

            self._close_bridge_locked()

            baudrate = int(self._config.get("baudrate", 115200))
            errors: list[str] = []
            for port in candidate_ports:
                bridge = ServoBridge(port=port, baudrate=baudrate)
                try:
                    bridge.open()
                except Exception as exc:
                    errors.append(f"{port}: {exc}")
                    try:
                        bridge.close()
                    except Exception:
                        pass
                    continue

                self._bridge = bridge
                self._connected_port = port
                self._config["port"] = port
                save_config(self.config_path, self._config)
                self.runtime_state.set_serial_port(port)
                self._refresh_status_locked()
                self.events.info("servo", f"Connected to Arduino bridge on {port}.")
                return

            self._last_error = "Could not connect to any candidate serial port."
            if errors:
                self._last_error += " " + " | ".join(errors)
            if not auto:
                self.events.error("servo", self._last_error)
            raise RuntimeError(self._last_error)

    def disconnect(self) -> None:
        with self._lock:
            if self._bridge is None:
                return
            current_port = self._connected_port
            self._close_bridge_locked()
            self.events.info("servo", f"Disconnected from Arduino bridge on {current_port}.")

    def save_config_only(self) -> None:
        with self._lock:
            save_config(self.config_path, self._config)
            self.events.info("servo", f"Saved calibration to {self.config_path}.")

    def update_profile(self, channel: int, updates: dict[str, Any], apply_now: bool = False) -> None:
        with self._lock:
            profile = resolve_profile(self._config, str(channel))

            if "name" in updates and str(updates["name"]).strip():
                profile["name"] = str(updates["name"]).strip()
            if "min_us" in updates and not is_blank(updates["min_us"]):
                profile["min_us"] = int(updates["min_us"])
            if "max_us" in updates and not is_blank(updates["max_us"]):
                profile["max_us"] = int(updates["max_us"])
            if "home_deg" in updates and not is_blank(updates["home_deg"]):
                profile["home_deg"] = float(updates["home_deg"])
            if "invert" in updates:
                profile["invert"] = bool(updates["invert"])

            if "states_us" in updates and isinstance(updates["states_us"], dict):
                states_us = profile_states(profile)
                for state_name in STATE_NAMES:
                    if state_name not in updates["states_us"]:
                        continue
                    value = updates["states_us"][state_name]
                    states_us[state_name] = None if is_blank(value) else int(value)

            validate_profile(profile)
            save_config(self.config_path, self._config)
            self.events.info(
                "servo",
                f"Updated profile for channel {profile['channel']} ({profile['name']}).",
            )

            if apply_now:
                self._with_bridge_locked(
                    f"Apply calibration for channel {profile['channel']}",
                    lambda bridge: bridge.set_calibration(profile),
                )

    def perform_action(self, payload: dict[str, Any]) -> str:
        action = str(payload.get("action", "")).strip()
        if not action:
            raise ValueError("Missing action.")

        with self._lock:
            if action == "apply_config":
                self._with_bridge_locked("Apply config", lambda bridge: bridge.apply_config(self._config))
                self.events.info("servo", "Applied all configured calibrations to the Arduino.")
                return "Applied all configured servo calibrations."

            if action == "save_config":
                self.save_config_only()
                return "Saved the local servo calibration file."

            if action == "home_all":
                self._with_bridge_locked(
                    "Home all configured servos",
                    lambda bridge: apply_to_all_configured(bridge, self._config, "home"),
                )
                self.events.info("servo", "Moved all configured servos to home.")
                return "Moved all configured servos home."

            if action == "enable_all":
                self._with_bridge_locked(
                    "Enable all configured servos",
                    lambda bridge: apply_to_all_configured(bridge, self._config, "enable"),
                )
                self.events.info("servo", "Enabled all configured servos.")
                return "Enabled all configured servos."

            if action == "disable_all":
                self._with_bridge_locked(
                    "Disable all configured servos",
                    lambda bridge: apply_to_all_configured(bridge, self._config, "disable"),
                )
                self.events.info("servo", "Disabled all configured servos.")
                return "Disabled all configured servos."

            if action == "cycle_all":
                cycles = clamp_int(payload.get("cycles", 1), 1, 100)
                steps = clamp_int(payload.get("steps", 40), 1, 400)
                delay_ms = clamp_int(payload.get("delay_ms", 40), 0, 5000)
                hold_ms = clamp_int(payload.get("hold_ms", 200), 0, 5000)
                self._with_bridge_locked(
                    "Cycle all configured servos",
                    lambda bridge: cycle_all_profiles(
                        bridge,
                        self._config,
                        cycles=cycles,
                        steps=steps,
                        delay_s=(delay_ms / 1000.0),
                        hold_s=(hold_ms / 1000.0),
                    ),
                )
                self.events.info(
                    "servo",
                    "Completed a cycle-all run.",
                    {
                        "cycles": cycles,
                        "steps": steps,
                        "delay_ms": delay_ms,
                        "hold_ms": hold_ms,
                    },
                )
                return f"Cycled all configured servos for {cycles} cycle(s)."

            target = str(payload.get("target", "")).strip()
            if not target:
                raise ValueError("Missing target.")

            profile = resolve_profile(self._config, target)
            label = f"channel {profile['channel']} ({profile['name']})"

            if action == "home":
                self._with_bridge_locked("Home servo", lambda bridge: bridge.home(str(int(profile["channel"]))))
                self.events.info("servo", f"Moved {label} home.")
                return f"Moved {label} home."

            if action == "enable":
                self._with_bridge_locked("Enable servo", lambda bridge: bridge.enable(str(int(profile["channel"]))))
                self.events.info("servo", f"Enabled {label}.")
                return f"Enabled {label}."

            if action == "disable":
                self._with_bridge_locked("Disable servo", lambda bridge: bridge.disable(str(int(profile["channel"]))))
                self.events.info("servo", f"Disabled {label}.")
                return f"Disabled {label}."

            if action == "angle":
                angle = float(payload["angle"])
                self._with_bridge_locked(
                    f"Move {label} by angle",
                    lambda bridge: bridge.move_angle(int(profile["channel"]), angle),
                )
                self.events.info("servo", f"Moved {label} to {angle:.1f} deg.")
                return f"Moved {label} to {angle:.1f} deg."

            if action == "pulse":
                pulse_us = int(payload["pulse_us"])
                self._with_bridge_locked(
                    f"Move {label} by pulse",
                    lambda bridge: bridge.set_pulse(int(profile["channel"]), pulse_us),
                )
                self.events.info("servo", f"Moved {label} to {pulse_us} us.")
                return f"Moved {label} to {pulse_us} us."

            if action == "nudge":
                delta_us = int(payload["delta_us"])
                current_pulse_us, target_pulse_us = self._with_bridge_locked(
                    f"Nudge {label}",
                    lambda bridge: nudge_profile(bridge, profile, delta_us),
                )
                self.events.info(
                    "servo",
                    f"Nudged {label} from {current_pulse_us} us to {target_pulse_us} us.",
                )
                return f"Nudged {label} from {current_pulse_us} us to {target_pulse_us} us."

            if action == "move_state":
                state_name = str(payload["state_name"])
                saved_pulse_us, effective_pulse_us = self._with_bridge_locked(
                    f"Move {label} to saved state",
                    lambda bridge: move_profile_to_saved_state(bridge, profile, state_name),
                )
                self.events.info(
                    "servo",
                    f"Moved {label} to {state_name} at {effective_pulse_us} us.",
                )
                if saved_pulse_us != effective_pulse_us:
                    return (
                        f"Moved {label} to {state_name} at {effective_pulse_us} us "
                        f"(saved value {saved_pulse_us} us was clamped)."
                    )
                return f"Moved {label} to {state_name} at {effective_pulse_us} us."

            if action == "capture_state":
                state_name = str(payload["state_name"])
                pulse_us = self._with_bridge_locked(
                    f"Capture saved state for {label}",
                    lambda bridge: capture_profile_state(bridge, profile, state_name),
                )
                save_config(self.config_path, self._config)
                self.events.info("servo", f"Captured {state_name} for {label} at {pulse_us} us.")
                return f"Captured {state_name} for {label} at {pulse_us} us."

            if action == "set_state":
                state_name = str(payload["state_name"])
                pulse_us = int(payload["pulse_us"])
                set_profile_state(profile, state_name, pulse_us)
                save_config(self.config_path, self._config)
                self.events.info("servo", f"Saved {state_name} for {label} at {pulse_us} us.")
                return f"Saved {state_name} for {label} at {pulse_us} us."

        raise ValueError(f"Unsupported action: {action}")

    def status_snapshot(self, try_auto_connect: bool = True) -> dict[str, Any]:
        with self._lock:
            if try_auto_connect:
                self._maybe_auto_connect_locked()

            ports, port_scan_error = self._list_ports_locked()
            live_rows = self._refresh_status_locked() if self._bridge is not None else []
            live_by_channel = {int(row["channel"]): row for row in live_rows}

            profiles = [
                serialize_profile(profile, live_by_channel.get(int(profile["channel"])))
                for profile in configured_profiles(self._config)
            ]

            enabled_count = sum(1 for profile in profiles if profile["live"] and profile["live"]["enabled"])
            saved_state_total = sum(int(profile["saved_state_count"]) for profile in profiles)

            return {
                "connected": self._bridge is not None,
                "port": self._connected_port,
                "configured_port": self._config.get("port"),
                "available_ports": ports,
                "port_scan_error": port_scan_error,
                "last_error": self._last_error,
                "last_status_at": self._last_status_at,
                "config_path": str(self.config_path),
                "servo_count": len(profiles),
                "enabled_count": enabled_count,
                "saved_state_total": saved_state_total,
                "profiles": profiles,
            }


class CameraService:
    def __init__(self, runtime_state: RuntimeStateStore, events: EventLog) -> None:
        self.runtime_state = runtime_state
        self.events = events
        self._lock = threading.RLock()
        self._capture: Any = None
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_event = threading.Event()
        self._latest_jpeg: Optional[bytes] = None
        self._frame_size: Optional[list[int]] = None
        self._resolved_device: Optional[str] = None
        self._last_frame_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_auto_start_attempt = 0.0

    def discover_devices(self) -> list[dict[str, Any]]:
        v4l2_devices = self._discover_linux_devices_v4l2()
        if v4l2_devices:
            return v4l2_devices

        devices: list[dict[str, Any]] = []

        if sys.platform.startswith("linux"):
            sysfs_root = Path("/sys/class/video4linux")
            for node in sorted(sysfs_root.glob("video*")):
                device_path = Path("/dev") / node.name
                if not device_path.exists():
                    continue
                label_path = node / "name"
                label = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else node.name
                devices.append(
                    {
                        "id": str(device_path),
                        "path": str(device_path),
                        "label": f"{label} ({device_path.name})",
                    }
                )

        if devices:
            return self._sort_linux_devices(devices)

        if not sys.platform.startswith("linux"):
            for index in range(4):
                devices.append(
                    {
                        "id": str(index),
                        "path": str(index),
                        "label": f"Camera {index}",
                    }
                )

        return devices

    def _discover_linux_devices_v4l2(self) -> list[dict[str, Any]]:
        if not sys.platform.startswith("linux"):
            return []

        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except Exception:
            return []

        devices: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        current_label = ""

        for raw_line in result.stdout.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                current_label = ""
                continue

            if line[:1].isspace():
                path = line.strip()
                if not path.startswith("/dev/video") or path in seen_paths:
                    continue
                seen_paths.add(path)
                label = current_label or Path(path).name
                devices.append(
                    {
                        "id": path,
                        "path": path,
                        "label": f"{label} ({Path(path).name})",
                    }
                )
                continue

            current_label = line.rstrip(":").strip()

        return self._sort_linux_devices(devices)

    def _sort_linux_devices(self, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not devices:
            return []

        preferred = [device for device in devices if self._is_capture_device_label(device["label"])]
        ordered = preferred if preferred else devices
        return sorted(ordered, key=self._linux_device_sort_key)

    def _is_capture_device_label(self, label: str) -> bool:
        lowered = label.lower()
        blocked_tokens = (
            "pispbe",
            "rpivid",
            "codec",
            "metadata",
            "stateless",
            "platform:1000880000.pisp_be",
            "platform:rpivid",
        )
        return not any(token in lowered for token in blocked_tokens)

    def _linux_device_sort_key(self, device: dict[str, Any]) -> tuple[int, int, str]:
        label = str(device.get("label", "")).lower()
        path = str(device.get("path", ""))

        priority = 0
        if any(token in label for token in ("usb", "webcam", "camera", "uvc")):
            priority -= 100
        if "hdmi usb" in label:
            priority -= 40
        if "platform:" in label:
            priority += 40
        if not self._is_capture_device_label(label):
            priority += 200

        match = re.search(r"video(\d+)$", path)
        index = int(match.group(1)) if match else 999
        return (priority, index, path)

    def _camera_settings_locked(self) -> dict[str, Any]:
        return self.runtime_state.snapshot()["camera"]

    def _release_capture_locked(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
        self._capture = None
        self._resolved_device = None

    def _open_capture_locked(self) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python-headless is not installed.")

        settings = self._camera_settings_locked()
        devices = self.discover_devices()
        selected_device = settings.get("device") or (devices[0]["id"] if devices else None)
        if selected_device is None:
            raise RuntimeError("No camera devices were found.")

        candidates: list[Any] = [selected_device]
        if isinstance(selected_device, str):
            if selected_device.isdigit():
                candidates.append(int(selected_device))
            match = re.search(r"video(\d+)$", selected_device)
            if match:
                candidates.append(int(match.group(1)))

        unique_candidates: list[Any] = []
        for candidate in candidates:
            if candidate not in unique_candidates:
                unique_candidates.append(candidate)

        last_error = "Could not open the selected camera."
        for candidate in unique_candidates:
            backend_attempts = [None]
            if sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
                backend_attempts = [cv2.CAP_V4L2, None]

            for backend in backend_attempts:
                try:
                    capture = cv2.VideoCapture(candidate) if backend is None else cv2.VideoCapture(candidate, backend)
                except Exception as exc:
                    last_error = str(exc)
                    continue

                if not capture or not capture.isOpened():
                    try:
                        capture.release()
                    except Exception:
                        pass
                    last_error = f"Camera {candidate!r} did not open."
                    continue

                try:
                    if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                        capture.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                    if hasattr(cv2, "CAP_PROP_FOURCC") and hasattr(cv2, "VideoWriter_fourcc"):
                        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                except Exception:
                    pass

                capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(settings["width"]))
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(settings["height"]))
                capture.set(cv2.CAP_PROP_FPS, int(settings["fps"]))

                for _ in range(12):
                    ok, frame = capture.read()
                    if ok and frame is not None:
                        encoded_ok, encoded = cv2.imencode(
                            ".jpg",
                            frame,
                            [int(cv2.IMWRITE_JPEG_QUALITY), int(settings["jpeg_quality"])],
                        )
                        if encoded_ok:
                            self._capture = capture
                            self._resolved_device = str(candidate)
                            self._latest_jpeg = encoded.tobytes()
                            self._frame_size = [int(frame.shape[1]), int(frame.shape[0])]
                            self._last_frame_at = utc_timestamp()
                            self._last_error = None
                            return
                    time.sleep(0.05)

                capture.release()
                last_error = f"Camera {candidate!r} opened but did not produce frames."

        raise RuntimeError(last_error)

    def _capture_loop(self) -> None:
        assert cv2 is not None
        consecutive_failures = 0
        while not self._stop_event.is_set():
            with self._lock:
                capture = self._capture
                settings = self._camera_settings_locked()

            if capture is None:
                time.sleep(0.15)
                continue

            ok, frame = capture.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 8:
                    with self._lock:
                        self._last_error = "Camera stopped delivering frames. Reopening stream."
                        self.events.warn("camera", self._last_error)
                        self._release_capture_locked()
                        try:
                            self._open_capture_locked()
                            consecutive_failures = 0
                            self.events.info(
                                "camera",
                                f"Reconnected webcam on {self._resolved_device}.",
                            )
                        except Exception as exc:
                            self._last_error = str(exc)
                    time.sleep(0.2)
                else:
                    time.sleep(0.03)
                continue

            consecutive_failures = 0
            encoded_ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(settings["jpeg_quality"])],
            )
            if not encoded_ok:
                with self._lock:
                    self._last_error = "Failed to encode a webcam frame."
                time.sleep(0.03)
                continue

            with self._lock:
                self._latest_jpeg = encoded.tobytes()
                self._frame_size = [int(frame.shape[1]), int(frame.shape[0])]
                self._last_frame_at = utc_timestamp()
                self._last_error = None
                self._frame_event.set()

        with self._lock:
            self._release_capture_locked()

    def ensure_running(self, auto: bool = False) -> None:
        with self._lock:
            if self._capture_thread is not None and self._capture_thread.is_alive() and self._capture is not None:
                return

            if auto:
                now = time.monotonic()
                if now - self._last_auto_start_attempt < AUTO_CONNECT_COOLDOWN_S:
                    return
                self._last_auto_start_attempt = now

            self._stop_event.clear()
            self._frame_event.clear()
            self._open_capture_locked()
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="camera-capture-loop",
                daemon=True,
            )
            self._capture_thread.start()
            self.events.info("camera", f"Started webcam stream on {self._resolved_device}.")

    def stop(self) -> None:
        thread: Optional[threading.Thread]
        with self._lock:
            self._stop_event.set()
            thread = self._capture_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        with self._lock:
            self._capture_thread = None
            self._stop_event = threading.Event()
            self._frame_event.clear()
            self._release_capture_locked()

    def restart(self) -> None:
        self.stop()
        self.ensure_running()

    def update_settings(self, settings: dict[str, Any]) -> None:
        applied = self.runtime_state.update_camera_settings(settings)
        self.events.info(
            "camera",
            "Updated camera settings.",
            {
                "device": applied.get("device"),
                "width": applied.get("width"),
                "height": applied.get("height"),
                "fps": applied.get("fps"),
                "jpeg_quality": applied.get("jpeg_quality"),
            },
        )
        self.restart()

    def get_frame(self, timeout_s: float = 3.0) -> bytes:
        self.ensure_running(auto=False)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest_jpeg is not None:
                    return self._latest_jpeg
            self._frame_event.wait(timeout=0.15)
        raise RuntimeError(self._last_error or "No webcam frame is available.")

    def status_snapshot(self, auto_start: bool = True) -> dict[str, Any]:
        devices = self.discover_devices()
        settings = self.runtime_state.snapshot()["camera"]
        should_auto_start = auto_start and devices and (
            sys.platform.startswith("linux") or not is_auto_blank(settings.get("device"))
        )
        if should_auto_start:
            try:
                self.ensure_running(auto=True)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
        with self._lock:
            backend_available = cv2 is not None
            return {
                "backend_available": backend_available,
                "streaming": self._capture is not None and self._latest_jpeg is not None,
                "device_source": "/dev/video* on the host Raspberry Pi" if sys.platform.startswith("linux") else "Development-host camera device",
                "device": settings.get("device"),
                "resolved_device": self._resolved_device,
                "width": int(settings["width"]),
                "height": int(settings["height"]),
                "fps": int(settings["fps"]),
                "jpeg_quality": int(settings["jpeg_quality"]),
                "frame_size": self._frame_size,
                "last_frame_at": self._last_frame_at,
                "last_error": self._last_error if backend_available else "opencv-python-headless is not installed.",
                "available_devices": devices,
            }


def build_dashboard(
    servo_service: ServoService,
    camera_service: CameraService,
    events: EventLog,
    runtime_state: RuntimeStateStore,
) -> dict[str, Any]:
    return {
        "server_time": utc_timestamp(),
        "host": server_host_metadata(),
        "servo": servo_service.status_snapshot(),
        "camera": camera_service.status_snapshot(),
        "events": events.tail(),
        "runtime_state_path": str(runtime_state.path),
    }


def create_app(
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_state_path: Path = DEFAULT_RUNTIME_STATE_PATH,
) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False

    runtime_state = RuntimeStateStore(runtime_state_path)
    events = EventLog()
    servo_service = ServoService(config_path=config_path, runtime_state=runtime_state, events=events)
    camera_service = CameraService(runtime_state=runtime_state, events=events)

    def ok_response(message: Optional[str] = None) -> Response:
        payload: dict[str, Any] = {
            "ok": True,
            "dashboard": build_dashboard(servo_service, camera_service, events, runtime_state),
        }
        if message:
            payload["message"] = message
        return jsonify(payload)

    def error_response(error: str, status_code: int = 400) -> tuple[Response, int]:
        payload = {
            "ok": False,
            "error": error,
            "dashboard": build_dashboard(servo_service, camera_service, events, runtime_state),
        }
        return jsonify(payload), status_code

    @app.after_request
    def add_no_store_headers(response: Response) -> Response:
        if request.path.startswith("/api/") or request.path.startswith("/camera/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/")
    def index() -> Response:
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/api/dashboard")
    def dashboard() -> Response:
        return ok_response()

    @app.route("/api/servo/connect", methods=["POST"])
    def servo_connect() -> Response | tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        port = payload.get("port")
        try:
            servo_service.connect(None if is_auto_blank(port) else str(port))
            return ok_response("Connected to the Arduino servo bridge.")
        except Exception as exc:
            return error_response(str(exc), 503)

    @app.route("/api/servo/disconnect", methods=["POST"])
    def servo_disconnect() -> Response:
        servo_service.disconnect()
        return ok_response("Disconnected from the Arduino servo bridge.")

    @app.route("/api/servo/profiles/<int:channel>", methods=["POST"])
    def update_servo_profile(channel: int) -> Response | tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        try:
            servo_service.update_profile(channel, payload, apply_now=bool(payload.get("apply_now")))
            return ok_response(f"Updated channel {channel} configuration.")
        except Exception as exc:
            return error_response(str(exc), 400)

    @app.route("/api/servo/command", methods=["POST"])
    def servo_command() -> Response | tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        try:
            message = servo_service.perform_action(payload)
            return ok_response(message)
        except Exception as exc:
            status = 503 if "failed:" in str(exc).lower() else 400
            return error_response(str(exc), status)

    @app.route("/api/camera/config", methods=["POST"])
    def camera_config() -> Response | tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        try:
            camera_service.update_settings(payload)
            return ok_response("Updated camera settings and restarted the stream.")
        except Exception as exc:
            return error_response(str(exc), 503)

    @app.route("/api/camera/restart", methods=["POST"])
    def camera_restart() -> Response | tuple[Response, int]:
        try:
            camera_service.restart()
            return ok_response("Restarted the camera stream.")
        except Exception as exc:
            return error_response(str(exc), 503)

    @app.route("/api/camera/snapshot.jpg")
    def camera_snapshot() -> Response | tuple[Response, int]:
        try:
            frame = camera_service.get_frame(timeout_s=3.0)
            return Response(frame, mimetype="image/jpeg")
        except Exception as exc:
            return error_response(str(exc), 503)

    @app.route("/camera/stream.mjpg")
    def camera_stream() -> Response:
        boundary = "frame"

        def generate() -> Iterator[bytes]:
            while True:
                try:
                    frame = camera_service.get_frame(timeout_s=5.0)
                except Exception:
                    time.sleep(0.4)
                    continue
                yield (
                    b"--" + boundary.encode("ascii") + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
                time.sleep(0.05)

        return Response(
            generate(),
            mimetype=f"multipart/x-mixed-replace; boundary={boundary}",
        )

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Marble Maze Raspberry Pi control center web app.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port to serve on.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Servo calibration JSON path (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_RUNTIME_STATE_PATH,
        help=f"Runtime state JSON path (default: {DEFAULT_RUNTIME_STATE_PATH}).",
    )
    parser.add_argument("--debug", action="store_true", help="Use Flask's debug server instead of Waitress.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    app = create_app(config_path=args.config, runtime_state_path=args.state)

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True, threaded=True)
        return 0

    try:
        from waitress import serve
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)
        return 0

    print(f"Serving Marble Maze Control Center on http://{args.host}:{args.port}")
    serve(app, host=args.host, port=args.port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
