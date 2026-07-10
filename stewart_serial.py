#!/usr/bin/env python3
"""Open the Stewart Uno serial port (best-effort DTR/RTS handling).

On Jetson + Uno R3 CDC-ACM, opening `/dev/ttyACM*` typically resets the board
once (kernel asserts DTR → RESET). That clears firmware `calibrated` / enable.
We do **not** use a hardware autoreset disable (no RESET–GND capacitor).

Host tools must therefore re-send `calibrate` after open when motion is needed
(cranks must still be in the physical reference pose). This helper still
deasserts DTR/RTS and clears HUPCL to reduce extra pulses on close.
"""

from __future__ import annotations

import termios
import time

import serial


def open_stewart_serial(
    port: str,
    baud: int = 115200,
    timeout: float = 0.2,
) -> serial.Serial:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = timeout
    # None = block until write completes. A short write_timeout causes
    # SerialTimeoutException when flooding pose/vel on CDC-ACM.
    ser.write_timeout = None
    ser.dsrdtr = False
    ser.rtscts = False
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.dtr = False
    ser.rts = False
    try:
        fd = ser.fileno()
        attrs = termios.tcgetattr(fd)
        attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass
    return ser


def wait_if_reset(ser: serial.Serial, wait_s: float = 2.2) -> bool:
    """If the board just rebooted, wait for it. Returns True if boot text seen."""
    end = time.time() + 0.4
    buf = b""
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            end = time.time() + 0.2
        else:
            time.sleep(0.05)
    text = buf.decode("utf-8", "replace")
    if "Stewart ready" in text or "UIM5756PM" in text:
        time.sleep(max(0.0, wait_s - 0.4))
        return True
    return False
