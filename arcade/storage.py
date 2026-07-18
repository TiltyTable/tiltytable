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

    def top_for_level(self, level_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = [
            row for row in self.all()
            if row.get("levelId") == level_id
            and int(row.get("scoringVersion", 1)) == 2
            and (row.get("scoreType") != "time" or bool(row.get("complete")))
        ]
        return self._rank_mode(rows)[: max(0, limit)]

    def leaderboards(
        self, level_ids: list[str] | tuple[str, ...], limit: int = 10
    ) -> dict[str, list[dict[str, Any]]]:
        rows = self.all()
        size = max(0, limit)
        return {
            level_id: self._rank_mode(
                [
                    row for row in rows
                    if row.get("levelId") == level_id
                    and int(row.get("scoringVersion", 1)) == 2
                    and (row.get("scoreType") != "time" or bool(row.get("complete")))
                ]
            )[:size]
            for level_id in level_ids
        }

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        clean = {
            "initials": str(entry["initials"]).strip().upper(),
            "score": max(0, int(entry["score"])),
            "levelsCleared": max(0, int(entry["levelsCleared"])),
            "gauntletLevelCount": max(1, int(entry.get("gauntletLevelCount", 3))),
            "elapsedMs": max(0, int(entry["elapsedMs"])),
            "complete": bool(entry.get("complete", False)),
            "createdAt": entry.get("createdAt")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if entry.get("levelId"):
            clean["levelId"] = str(entry["levelId"])
            clean["levelTitle"] = str(entry.get("levelTitle", entry["levelId"]))
            clean["scoreType"] = str(entry.get("scoreType", "points"))
            clean["scoringVersion"] = int(entry.get("scoringVersion", 2))
        if len(clean["initials"]) != 3 or not clean["initials"].isalpha():
            raise ValueError("initials must be exactly three letters")
        if clean["levelsCleared"] < 1 and "levelId" not in clean:
            raise ValueError("at least one completed level is required")

        with self._lock:
            rows = self._load_unlocked()
            rows.append(clean)
            if "levelId" in clean:
                level_id = clean["levelId"]
                other = [row for row in rows if row.get("levelId") != level_id]
                same_level = self._rank_mode(
                    [row for row in rows if row.get("levelId") == level_id]
                )[: self.limit]
                rows = self._rank(other + same_level)
            else:
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

    @staticmethod
    def _rank_mode(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: (
                0 if row.get("scoreType") == "time" else 1,
                (
                    int(row.get("elapsedMs", row.get("score", 0)))
                    if row.get("scoreType") == "time"
                    else -int(row.get("score", 0))
                ),
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
