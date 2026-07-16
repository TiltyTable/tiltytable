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
    ken_line: str
    troll_line: str
    mode: str | None = None
    survival_seconds: float | None = None
    dwell_seconds: float | None = None
    warn_seconds: float | None = None
    points_per_tile: int | None = None
    pit_confirm_seconds: float | None = None
    mode_params: dict[str, Any] | None = None
    seed: int = 1

    @property
    def is_survival_lava(self) -> bool:
        return self.mode == "survival_lava"

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "subtitle": self.subtitle,
            "timeLimitSeconds": self.time_limit_seconds,
            "startCell": self.start_cell,
            "endCell": self.end_cell,
            "feature": self.feature,
            "rules": list(self.rules),
            "kenLine": self.ken_line,
            "trollLine": self.troll_line,
        }
        if self.mode:
            payload["mode"] = self.mode
            payload["modeParams"] = dict(self.mode_params or {})
            payload["seed"] = self.seed
        if self.is_survival_lava:
            payload["survivalSeconds"] = self.survival_seconds
            payload["dwellSeconds"] = self.dwell_seconds
            payload["warnSeconds"] = self.warn_seconds
            payload["pointsPerTile"] = self.points_per_tile
            if self.pit_confirm_seconds is not None:
                payload["pitConfirmSeconds"] = self.pit_confirm_seconds
        return payload


@dataclass(frozen=True)
class LevelCatalog:
    levels: tuple[Level, ...]
    gauntlet_level_ids: tuple[str, ...]
    lore: dict[str, str]

    @property
    def gauntlet_level_count(self) -> int:
        return len(self.gauntlet_level_ids)

    def gauntlet_indices(self) -> tuple[int, ...]:
        id_to_index = {level.id: index for index, level in enumerate(self.levels)}
        return tuple(id_to_index[level_id] for level_id in self.gauntlet_level_ids)

    def public_dict(self) -> dict[str, Any]:
        return {
            "gauntletLevelIds": list(self.gauntlet_level_ids),
            "gauntletLevelCount": self.gauntlet_level_count,
            "totalLevels": len(self.levels),
            "lore": self.lore,
        }


def load_levels(path: Path = MANIFEST_PATH) -> LevelCatalog:
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
        mode = str(item["mode"]) if item.get("mode") else None
        mode_params = dict(item.get("modeParams", {}))
        if mode == "survival_lava" and not mode_params:
            for key in (
                "survivalSeconds", "dwellSeconds", "warnSeconds",
                "pointsPerTile", "pitConfirmSeconds",
            ):
                if item.get(key) is not None:
                    mode_params[key] = item[key]
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
            ken_line=str(item.get("kenLine", "")),
            troll_line=str(item.get("trollLine", "")),
            mode=mode,
            survival_seconds=(
                float(item.get("survivalSeconds", mode_params.get("survivalSeconds")))
                if item.get("survivalSeconds", mode_params.get("survivalSeconds")) is not None
                else None
            ),
            dwell_seconds=(
                float(item.get("dwellSeconds", mode_params.get("dwellSeconds")))
                if item.get("dwellSeconds", mode_params.get("dwellSeconds")) is not None
                else None
            ),
            warn_seconds=(
                float(item.get("warnSeconds", mode_params.get("warnSeconds")))
                if item.get("warnSeconds", mode_params.get("warnSeconds")) is not None
                else None
            ),
            points_per_tile=(
                int(item.get("pointsPerTile", mode_params.get("pointsPerTile")))
                if item.get("pointsPerTile", mode_params.get("pointsPerTile")) is not None
                else None
            ),
            pit_confirm_seconds=(
                float(item.get("pitConfirmSeconds", mode_params.get("pitConfirmSeconds")))
                if item.get("pitConfirmSeconds", mode_params.get("pitConfirmSeconds")) is not None
                else None
            ),
            mode_params=mode_params,
            seed=int(item.get("seed", 1)),
        )
        validate_level(level)
        levels.append(level)
    if len(levels) < 3:
        raise ValueError(f"arcade requires at least 3 levels, found {len(levels)}")
    gauntlet_level_ids = tuple(str(level_id) for level_id in raw.get("gauntletLevelIds", []))
    if len(gauntlet_level_ids) < 2:
        raise ValueError("gauntletLevelIds must list at least 2 levels")
    level_ids = {level.id for level in levels}
    missing = [level_id for level_id in gauntlet_level_ids if level_id not in level_ids]
    if missing:
        raise ValueError(f"unknown gauntlet level ids: {missing}")
    lore = {str(key): str(value) for key, value in raw.get("lore", {}).items()}
    ordered = tuple(sorted(levels, key=lambda level: level.number))
    return LevelCatalog(
        levels=ordered,
        gauntlet_level_ids=gauntlet_level_ids,
        lore=lore,
    )


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
    if level.is_survival_lava:
        if level.survival_seconds is None or level.survival_seconds <= 0:
            raise ValueError(f"{level.id}: survivalSeconds must be positive")
        if level.dwell_seconds is None or level.dwell_seconds <= 0:
            raise ValueError(f"{level.id}: dwellSeconds must be positive")
        if level.warn_seconds is None or level.warn_seconds <= 0:
            raise ValueError(f"{level.id}: warnSeconds must be positive")
        if level.points_per_tile is None or level.points_per_tile < 0:
            raise ValueError(f"{level.id}: pointsPerTile must be >= 0")
        if level.time_limit_seconds < int(level.survival_seconds):
            raise ValueError(
                f"{level.id}: timeLimitSeconds must be >= survivalSeconds"
            )
    if level.mode in ("hex_fall", "target_hunt") and not level.mode_params:
        raise ValueError(f"{level.id}: {level.mode} requires modeParams")


def load_map(level: Level) -> dict[str, dict[str, Any]]:
    return json.loads(level.map_path.read_text(encoding="utf-8"))
