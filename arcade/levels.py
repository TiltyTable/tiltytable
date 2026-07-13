from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path(__file__).with_name("levels.json")


@dataclass(frozen=True)
class Level:
    id: str
    number: int
    title: str
    subtitle: str
    map_path: Path
    time_limit_seconds: int
    start_cell: str
    end_cell: str
    feature: str
    rules: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "subtitle": self.subtitle,
            "timeLimitSeconds": self.time_limit_seconds,
            "startCell": self.start_cell,
            "endCell": self.end_cell,
            "feature": self.feature,
            "rules": list(self.rules),
        }


def load_levels(path: Path = MANIFEST_PATH) -> list[Level]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    levels: list[Level] = []
    seen: set[str] = set()
    for item in raw.get("levels", []):
        level_id = str(item["id"])
        if level_id in seen:
            raise ValueError(f"duplicate level id: {level_id}")
        seen.add(level_id)
        map_path = ROOT / str(item["map"])
        if not map_path.exists():
            raise ValueError(f"{level_id}: map does not exist: {map_path}")
        level = Level(
            id=level_id,
            number=int(item["number"]),
            title=str(item["title"]),
            subtitle=str(item["subtitle"]),
            map_path=map_path,
            time_limit_seconds=int(item["timeLimitSeconds"]),
            start_cell=str(item["startCell"]).upper(),
            end_cell=str(item["endCell"]).upper(),
            feature=str(item["feature"]),
            rules=tuple(str(rule) for rule in item["rules"]),
        )
        validate_level(level)
        levels.append(level)
    if len(levels) != 3:
        raise ValueError(f"arcade gauntlet requires exactly 3 levels, found {len(levels)}")
    return sorted(levels, key=lambda level: level.number)


def validate_level(level: Level) -> None:
    raw = json.loads(level.map_path.read_text(encoding="utf-8"))
    expected = {f"{chr(65 + col)}{row}" for row in range(1, 13) for col in range(12)}
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{level.id}: map must contain A1..L12; missing={missing[:5]} extra={extra[:5]}"
        )
    if level.start_cell not in raw or level.end_cell not in raw:
        raise ValueError(f"{level.id}: invalid start/end cell")
    if level.start_cell == level.end_cell:
        raise ValueError(f"{level.id}: start and end must differ")
    if level.time_limit_seconds <= 0:
        raise ValueError(f"{level.id}: time limit must be positive")


def load_map(level: Level) -> dict[str, dict[str, Any]]:
    return json.loads(level.map_path.read_text(encoding="utf-8"))

