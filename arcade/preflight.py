from __future__ import annotations

import argparse
import os
import shutil
import socket
from pathlib import Path

from game_runner import load_table_configs

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

    levels = load_levels()
    check_port(args.port)
    if args.hardware:
        check_hardware(args.module_port)
    print(f"preflight: {len(levels)} levels valid")
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

