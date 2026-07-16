#!/usr/bin/env python3
"""Query or change experimental crank speed/acceleration through supervisor."""

from __future__ import annotations

import argparse
from pathlib import Path

from stewart_supervisor_client import DEFAULT_SOCKET, StewartSupervisorClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--speed", type=float)
    parser.add_argument("--accel", type=float)
    args = parser.parse_args()

    if (args.speed is None) != (args.accel is None):
        parser.error("--speed and --accel must be supplied together")
    if args.speed is not None and not 1.0 <= args.speed <= 90.0:
        parser.error("--speed must be in [1, 90] deg/s")
    if args.accel is not None and not 1.0 <= args.accel <= 500.0:
        parser.error("--accel must be in [1, 500] deg/s^2")

    client = StewartSupervisorClient(args.socket, mode="motion")
    client.open()
    try:
        identity = client.exchange("EXP?")
        if not identity.startswith("OK EXP UIM5756PM_STEWART_EXP"):
            raise RuntimeError(f"wrong firmware: {identity!r}")
        # Always capture the supervisor-owned absolute motor coordinates before
        # issuing any command, even though PROFILE itself does not move them.
        print(client.exchange("STATUS"))
        if args.speed is not None:
            print(client.exchange(f"PROFILE {args.speed:.3f} {args.accel:.3f}"))
        print(client.exchange("PROFILE?"))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
