from __future__ import annotations

import argparse
import os
import shutil
import socket
from pathlib import Path

from game_runner import load_table_configs
from stewart_platform_control_common import find_trackball
from stewart_supervisor_client import DEFAULT_SOCKET

from .hardware import ModuleGridHardware
from .levels import load_levels


def check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"HTTP port {port} is already in use") from exc


def check_hardware(module_port: str) -> None:
    path = Path(module_port)
    if not path.exists():
        raise RuntimeError(f"module controller not found: {module_port}")
    if not os.access(path, os.R_OK | os.W_OK):
        raise RuntimeError(f"module controller is not readable/writable: {module_port}")
    led, servo_grid, servo_configs = load_table_configs()
    ModuleGridHardware._validate_calibration(led, servo_grid, servo_configs)
    if not DEFAULT_SOCKET.exists():
        raise RuntimeError(f"Stewart supervisor is not ready: {DEFAULT_SOCKET}")
    trackball = find_trackball()
    if trackball is None or not os.access(trackball, os.R_OK):
        raise RuntimeError("roller ball input device is not readable")
    try:
        from pyk4a import connected_device_count
    except ImportError as exc:
        raise RuntimeError("pyk4a is not installed") from exc
    if connected_device_count() < 1:
        raise RuntimeError("Azure Kinect was not found")


def find_browser() -> str | None:
    for executable in ("chromium-browser", "chromium", "firefox"):
        if shutil.which(executable):
            return executable
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate arcade launch resources")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--module-port", default="/dev/arduino-modules")
    parser.add_argument("--check-browser", action="store_true")
    args = parser.parse_args(argv)

    catalog = load_levels()
    check_port(args.port)
    if args.hardware:
        check_hardware(args.module_port)
    print(f"preflight: {len(catalog.levels)} levels valid ({catalog.gauntlet_level_count} gauntlet)")
    print("preflight: module calibration valid" if args.hardware else "preflight: simulation mode")
    if args.check_browser:
        browser = find_browser()
        if browser:
            print(f"preflight: browser={browser}")
        else:
            print("preflight: warning — no Chromium/Firefox installation found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

