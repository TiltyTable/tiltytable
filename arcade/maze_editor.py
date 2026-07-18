from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


CELL_KEYS = tuple(
    f"{chr(65 + col)}{row}" for row in range(1, 13) for col in range(12)
)
CELL_KEY_SET = set(CELL_KEYS)
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class MazeValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _number(
    value: Any,
    label: str,
    errors: list[str],
    *,
    allow_zero: bool = False,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label} must be numeric")
        return None
    if value < 0 if allow_zero else value <= 0:
        errors.append(f"{label} must be {'>= 0' if allow_zero else '> 0'}")
        return None
    return float(value)


def _color(value: Any, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or HEX_COLOR.fullmatch(value) is None:
        errors.append(f"{label} must be #RRGGBB")
        return None
    return value.upper()


def validate_maze_cells(cells: Any) -> dict[str, dict[str, Any]]:
    errors: list[str] = []
    if not isinstance(cells, dict):
        raise MazeValidationError(["cells must be an object"])
    actual = set(cells)
    if actual != CELL_KEY_SET:
        missing = sorted(CELL_KEY_SET - actual)
        extra = sorted(actual - CELL_KEY_SET)
        errors.append(
            "cells must contain exactly A1..L12; "
            f"missing={missing[:5]} extra={extra[:5]}"
        )

    normalized: dict[str, dict[str, Any]] = {}
    for key in CELL_KEYS:
        cell = cells.get(key)
        if not isinstance(cell, dict):
            if key in cells:
                errors.append(f"{key} must be an object")
            continue
        value = cell.get("value")
        if isinstance(value, bool) or value not in (-1, 0, 1):
            errors.append(f"{key}.value must be -1, 0, or 1")
        color = _color(cell.get("color"), f"{key}.color", errors)
        clean: dict[str, Any] = {
            "value": value if value in (-1, 0, 1) and not isinstance(value, bool) else 0,
            "color": color or "#000000",
        }

        dynamic = cell.get("dynamic")
        if dynamic is not None:
            if not isinstance(dynamic, dict):
                errors.append(f"{key}.dynamic must be an object")
            else:
                kind = str(dynamic.get("type", "cycle"))
                if kind == "cycle":
                    interval = _number(
                        dynamic.get("intervalSeconds"),
                        f"{key}.dynamic.intervalSeconds",
                        errors,
                    )
                    pattern = dynamic.get("pattern")
                    clean_pattern: list[dict[str, Any]] = []
                    if not isinstance(pattern, list) or len(pattern) < 2:
                        errors.append(f"{key}.dynamic.pattern needs at least 2 states")
                    else:
                        for index, state in enumerate(pattern):
                            label = f"{key}.dynamic.pattern[{index}]"
                            if not isinstance(state, dict):
                                errors.append(f"{label} must be an object")
                                continue
                            state_value = state.get("value")
                            if isinstance(state_value, bool) or state_value not in (-1, 0, 1):
                                errors.append(f"{label}.value must be -1, 0, or 1")
                            state_color = _color(state.get("color"), f"{label}.color", errors)
                            clean_pattern.append({
                                "value": (
                                    state_value
                                    if state_value in (-1, 0, 1)
                                    and not isinstance(state_value, bool)
                                    else 0
                                ),
                                "color": state_color or "#000000",
                            })
                    clean["dynamic"] = {
                        "type": "cycle",
                        "intervalSeconds": interval or 1.0,
                        "pattern": clean_pattern,
                    }
                elif kind == "delayed_trap":
                    arm = _number(
                        dynamic.get("armDelaySeconds"),
                        f"{key}.dynamic.armDelaySeconds",
                        errors,
                        allow_zero=True,
                    )
                    warn = _number(
                        dynamic.get("warnDurationSeconds"),
                        f"{key}.dynamic.warnDurationSeconds",
                        errors,
                    )
                    initial = _number(
                        dynamic.get("initialIntervalSeconds"),
                        f"{key}.dynamic.initialIntervalSeconds",
                        errors,
                    )
                    minimum = _number(
                        dynamic.get("minIntervalSeconds"),
                        f"{key}.dynamic.minIntervalSeconds",
                        errors,
                    )
                    if initial is not None and minimum is not None and minimum > initial:
                        errors.append(
                            f"{key}.dynamic.minIntervalSeconds must be <= initialIntervalSeconds"
                        )
                    trap = _color(dynamic.get("trapColor"), f"{key}.dynamic.trapColor", errors)
                    floor = _color(dynamic.get("floorColor"), f"{key}.dynamic.floorColor", errors)
                    clean["dynamic"] = {
                        "type": "delayed_trap",
                        "armDelaySeconds": arm if arm is not None else 0.0,
                        "warnDurationSeconds": warn or 1.0,
                        "initialIntervalSeconds": initial or 1.0,
                        "minIntervalSeconds": minimum or 0.1,
                        "trapColor": trap or "#FF0000",
                        "floorColor": floor or clean["color"],
                    }
                else:
                    errors.append(f"{key}.dynamic.type must be cycle or delayed_trap")
        normalized[key] = clean

    if errors:
        raise MazeValidationError(errors)
    return normalized


def load_maze(path: Path) -> dict[str, dict[str, Any]]:
    return validate_maze_cells(json.loads(Path(path).read_text(encoding="utf-8")))


def save_maze(path: Path, cells: Any) -> dict[str, dict[str, Any]]:
    path = Path(path)
    normalized = validate_maze_cells(cells)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(normalized, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return deepcopy(normalized)
