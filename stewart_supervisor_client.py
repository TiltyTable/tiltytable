"""Unix-socket client for the persistent Stewart supervisor."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

DEFAULT_SOCKET = Path(
    os.environ.get(
        "TILTYTABLE_STEWART_SOCKET",
        f"/run/user/{os.getuid()}/tiltytable-stewart.sock",
    )
)


class StewartSupervisorClient:
    def __init__(
        self,
        socket_path: Path = DEFAULT_SOCKET,
        *,
        mode: str = "motion",
    ) -> None:
        if mode not in ("readonly", "motion"):
            raise ValueError("mode must be readonly or motion")
        self.socket_path = socket_path
        self.mode = mode
        self.sock: socket.socket | None = None
        self.stream = None

    @property
    def is_open(self) -> bool:
        return self.sock is not None

    def open(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(str(self.socket_path))
            self.stream = self.sock.makefile("rwb", buffering=0)
            response = self._request({"action": "acquire", "mode": self.mode})
            if not response.get("ok"):
                raise RuntimeError(str(response))
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        if self.stream is None:
            raise RuntimeError("supervisor client is not open")
        self.stream.write(
            (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        )
        self.stream.flush()
        raw = self.stream.readline()
        if not raw:
            raise ConnectionError("Stewart supervisor disconnected")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError(f"invalid supervisor response: {response!r}")
        return response

    def exchange(self, command: str, timeout: float = 1.0) -> str:
        response = self._request(
            {"action": "command", "command": command, "timeout": timeout}
        )
        if not response.get("ok"):
            raise RuntimeError(
                f"supervisor {response.get('error')}: {response.get('message')}"
            )
        return str(response.get("reply", ""))

    def ping(self) -> bool:
        return bool(self._request({"action": "ping"}).get("ok"))
