from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "var" / "arcade" / "scores.json"


class ScoreStore:
    def __init__(self, path: Path = DEFAULT_PATH, limit: int = 50) -> None:
        self.path = Path(path)
        self.limit = limit
        self._lock = threading.Lock()

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._load_unlocked()

    def top(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.all()[: max(0, limit)]

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        clean = {
            "initials": str(entry["initials"]).strip().upper(),
            "score": max(0, int(entry["score"])),
            "levelsCleared": max(0, int(entry["levelsCleared"])),
            "gauntletLevelCount": max(1, int(entry.get("gauntletLevelCount", 3))),
            "elapsedMs": max(0, int(entry["elapsedMs"])),
            "restarts": max(0, int(entry.get("restarts", 0))),
            "complete": bool(entry.get("complete", False)),
            "createdAt": entry.get("createdAt")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if len(clean["initials"]) != 3 or not clean["initials"].isalpha():
            raise ValueError("initials must be exactly three letters")
        if clean["levelsCleared"] < 1:
            raise ValueError("at least one completed level is required")

        with self._lock:
            rows = self._load_unlocked()
            rows.append(clean)
            rows = self._rank(rows)[: self.limit]
            self._write_unlocked(rows)
        return clean

    def clear(self) -> None:
        with self._lock:
            self._write_unlocked([])

    @staticmethod
    def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: (
                -int(row.get("levelsCleared", 0)),
                -int(row.get("score", 0)),
                int(row.get("elapsedMs", 0)),
                str(row.get("createdAt", "")),
            ),
        )

    def _load_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw.get("scores", []) if isinstance(raw, dict) else []
        return self._rank([row for row in rows if isinstance(row, dict)])

    def _write_unlocked(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "scores": rows}
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

