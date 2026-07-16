#!/usr/bin/env python3
"""Persistent single-owner Stewart serial supervisor.

The supervisor keeps one Arduino CLI monitor process open with DTR/RTS disabled
and exposes newline-delimited JSON RPC on a local Unix socket. Motion clients
may come and go without reopening /dev/arduino-stewart.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

DEFAULT_SOCKET = Path(
    os.environ.get(
        "TILTYTABLE_STEWART_SOCKET",
        f"/run/user/{os.getuid()}/tiltytable-stewart.sock",
    )
)
DEFAULT_LOCK = Path(
    os.environ.get(
        "TILTYTABLE_STEWART_LOCK",
        f"/run/user/{os.getuid()}/tiltytable-stewart.lock",
    )
)


class Backend(Protocol):
    def transact(self, command: str, timeout: float = 1.0) -> str: ...

    def close(self) -> None: ...


class ArduinoCliBackend:
    """Persistent arduino-cli monitor configured not to assert DTR/RTS."""

    def __init__(
        self,
        port: str,
        baud: int,
        *,
        dtr: str = "on",
        rts: str = "off",
        arduino_cli: str = "arduino-cli",
    ) -> None:
        command = [
            arduino_cli,
            "monitor",
            "--quiet",
            "--raw",
            "-p",
            port,
            "-c",
            f"baudrate={baud},dtr={dtr},rts={rts}",
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._responses: queue.Queue[str] = queue.Queue()
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(
            target=self._read_stdout, daemon=True, name="stewart-monitor-reader"
        )
        self._errors = threading.Thread(
            target=self._read_stderr, daemon=True, name="stewart-monitor-stderr"
        )
        self._reader.start()
        self._errors.start()
        time.sleep(0.25)
        if self.process.poll() is not None:
            raise RuntimeError(
                f"arduino-cli monitor exited with {self.process.returncode}"
            )

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        while True:
            raw = self.process.stdout.readline()
            if not raw:
                return
            text = raw.decode("utf-8", "replace").strip()
            if text:
                self._responses.put(text)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        while True:
            raw = self.process.stderr.readline()
            if not raw:
                return
            text = raw.decode("utf-8", "replace").strip()
            if text:
                print(f"monitor: {text}", file=sys.stderr, flush=True)

    def _drain(self) -> None:
        while True:
            try:
                self._responses.get_nowait()
            except queue.Empty:
                return

    def transact(self, command: str, timeout: float = 1.0) -> str:
        if "\n" in command or "\r" in command or len(command) > 200:
            raise ValueError("invalid serial command")
        if self.process.poll() is not None:
            raise RuntimeError("arduino-cli monitor is not running")
        assert self.process.stdin is not None
        with self._write_lock:
            self._drain()
            self.process.stdin.write((command + "\n").encode("ascii"))
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            ignored: list[str] = []
            while time.monotonic() < deadline:
                try:
                    line = self._responses.get(
                        timeout=min(0.1, max(0.0, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    continue
                if line.startswith("OK ") or line.startswith("ERR "):
                    return line
                ignored.append(line)
            suffix = f"; ignored={ignored!r}" if ignored else ""
            raise TimeoutError(f"no Arduino reply for {command!r}{suffix}")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2.0)


class StewartSupervisor:
    def __init__(
        self,
        backend: Backend,
        socket_path: Path = DEFAULT_SOCKET,
    ) -> None:
        self.backend = backend
        self.socket_path = socket_path
        self.running = True

    @staticmethod
    def _reply(stream, payload: dict[str, object]) -> None:
        stream.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        stream.flush()

    def _handle_client(self, connection: socket.socket) -> None:
        mode: str | None = None
        motion_active = False
        stream = connection.makefile("rwb", buffering=0)
        try:
            while self.running:
                raw = stream.readline()
                if not raw:
                    return
                try:
                    request = json.loads(raw)
                    action = request.get("action")
                    if action == "acquire":
                        requested = request.get("mode")
                        if mode is not None or requested not in ("readonly", "motion"):
                            raise ValueError("invalid acquire")
                        mode = requested
                        self._reply(
                            stream,
                            {"ok": True, "supervisor": "ready", "mode": mode},
                        )
                        continue
                    if action == "ping":
                        self._reply(stream, {"ok": True, "supervisor": "alive"})
                        continue
                    if action != "command" or mode is None:
                        raise ValueError("acquire before sending commands")
                    command = str(request.get("command", ""))
                    timeout = float(request.get("timeout", 1.0))
                    if mode == "readonly" and command.upper().split(" ", 1)[0] not in {
                        "EXP?",
                        "STATUS",
                        "HELP",
                        "?",
                    }:
                        raise PermissionError("readonly lease cannot command motion")
                    reply = self.backend.transact(command, timeout=timeout)
                    upper = command.upper()
                    if mode == "motion" and (
                        upper.startswith("ARM ")
                        or upper.startswith("TARGET ")
                        or upper.startswith("CAL JOG ")
                    ):
                        motion_active = True
                    if upper in ("HOLD", "ABORT", "DISABLE"):
                        motion_active = False
                    self._reply(stream, {"ok": True, "reply": reply})
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    try:
                        self._reply(
                            stream,
                            {
                                "ok": False,
                                "error": type(exc).__name__,
                                "message": str(exc),
                            },
                        )
                    except (BrokenPipeError, ConnectionResetError):
                        return
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if mode == "motion" and motion_active:
                try:
                    self.backend.transact("ABORT", timeout=1.0)
                except Exception as exc:
                    print(
                        f"WARNING: client-loss ABORT failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
            try:
                stream.close()
            except OSError:
                pass

    def serve(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(1)
        server.settimeout(0.5)
        print(f"Stewart supervisor: {self.socket_path}", flush=True)
        try:
            while self.running:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                try:
                    self._handle_client(connection)
                finally:
                    connection.close()
        finally:
            server.close()
            if self.socket_path.exists():
                self.socket_path.unlink()
            self.backend.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/arduino-stewart")
    parser.add_argument("--baud", type=int, default=230400)
    parser.add_argument("--dtr", choices=("on", "off"), default="on")
    parser.add_argument("--rts", choices=("on", "off"), default="off")
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--arduino-cli", default="arduino-cli")
    args = parser.parse_args()

    args.lock.parent.mkdir(parents=True, exist_ok=True)
    lock_file = args.lock.open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another Stewart supervisor already owns the lock.", file=sys.stderr)
        return 2

    backend = ArduinoCliBackend(
        args.port,
        args.baud,
        dtr=args.dtr,
        rts=args.rts,
        arduino_cli=args.arduino_cli,
    )
    supervisor = StewartSupervisor(backend, args.socket)

    def stop(_signum, _frame) -> None:
        supervisor.running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        supervisor.serve()
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
