"""Portable browser/editor level packages and manifest/map compiler."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .levels import MANIFEST_PATH, ROOT

PACKAGE_VERSION = 1
SUPPORTED_MODES = {"reach_end", "survival_lava", "hex_fall", "target_hunt"}
CELL_KEYS = tuple(
    f"{chr(65 + col)}{row}" for row in range(1, 13) for col in range(12)
)
CELL_KEY_SET = set(CELL_KEYS)
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "reach_end": {},
    "survival_lava": {
        "survivalSeconds": 40.0,
        "dwellSeconds": 1.5,
        "warnSeconds": 2.0,
        "pointsPerTile": 25,
        "pitConfirmSeconds": 0.5,
    },
    "hex_fall": {
        "survivalSeconds": 45.0,
        "pitConfirmSeconds": 0.5,
        "collapseEverySeconds": 3.0,
        "collapseCount": 1,
        "collapseWarnSeconds": 1.0,
    },
    "target_hunt": {
        "startingSeconds": 20.0,
        "targetBonusSeconds": 5.0,
        "targetConfirmSeconds": 0.3,
        "pointsPerTarget": 100,
        "spawnPitCount": 1,
        "spawnWallCount": 1,
    },
}


class PackageValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def blank_cells() -> dict[str, dict[str, Any]]:
    return {
        key: {"value": 0, "color": "#567DBB"}
        for key in CELL_KEYS
    }


def blank_package(mode: str = "reach_end") -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    return {
        "version": PACKAGE_VERSION,
        "seed": 1,
        "meta": {
            "id": "new-level",
            "number": 1,
            "title": "New Chamber",
            "subtitle": "Describe this challenge",
            "timeLimitSeconds": 60,
            "startCell": "A1",
            "endCell": "L12",
            "feature": "Describe what changes on the physical table.",
            "rules": ["Guide the ball through the chamber."],
            "kenLine": "I'll explain the rules when you're ready.",
            "trollLine": "You built this trap yourself!",
        },
        "mode": mode,
        "modeParams": deepcopy(MODE_DEFAULTS[mode]),
        "cells": blank_cells(),
    }


def _positive_number(
    params: dict[str, Any], key: str, errors: list[str], *, allow_zero: bool = False
) -> None:
    value = params.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"modeParams.{key} must be numeric")
    elif value < 0 if allow_zero else value <= 0:
        comparator = ">= 0" if allow_zero else "> 0"
        errors.append(f"modeParams.{key} must be {comparator}")


def validate_package(package: dict[str, Any], *, raise_on_error: bool = True) -> list[str]:
    errors: list[str] = []
    if package.get("version") != PACKAGE_VERSION:
        errors.append(f"version must be {PACKAGE_VERSION}")
    if not isinstance(package.get("seed"), int):
        errors.append("seed must be an integer")
    mode = package.get("mode")
    if mode not in SUPPORTED_MODES:
        errors.append(f"mode must be one of {sorted(SUPPORTED_MODES)}")
    meta = package.get("meta")
    if not isinstance(meta, dict):
        errors.append("meta must be an object")
        meta = {}
    required_text = (
        "id", "title", "subtitle", "feature", "kenLine", "trollLine"
    )
    for key in required_text:
        if not isinstance(meta.get(key), str) or not meta.get(key, "").strip():
            errors.append(f"meta.{key} must be non-empty text")
    if not isinstance(meta.get("number"), int) or meta.get("number", 0) <= 0:
        errors.append("meta.number must be a positive integer")
    if (
        not isinstance(meta.get("timeLimitSeconds"), (int, float))
        or meta.get("timeLimitSeconds", 0) <= 0
    ):
        errors.append("meta.timeLimitSeconds must be positive")
    rules = meta.get("rules")
    if not isinstance(rules, list) or not rules or not all(
        isinstance(rule, str) and rule.strip() for rule in rules
    ):
        errors.append("meta.rules must contain non-empty text rules")
    for name in ("startCell", "endCell"):
        if meta.get(name) not in CELL_KEY_SET:
            errors.append(f"meta.{name} must be A1..L12")
    if mode == "reach_end" and meta.get("startCell") == meta.get("endCell"):
        errors.append("reach_end startCell and endCell must differ")

    cells = package.get("cells")
    if not isinstance(cells, dict):
        errors.append("cells must be an object")
        cells = {}
    actual = set(cells)
    if actual != CELL_KEY_SET:
        missing = sorted(CELL_KEY_SET - actual)
        extra = sorted(actual - CELL_KEY_SET)
        errors.append(
            f"cells must contain exactly A1..L12; "
            f"missing={missing[:5]} extra={extra[:5]}"
        )
    for key, cell in cells.items():
        if key not in CELL_KEY_SET or not isinstance(cell, dict):
            continue
        if cell.get("value") not in (-1, 0, 1):
            errors.append(f"cells.{key}.value must be -1, 0, or 1")
        if not isinstance(cell.get("color"), str) or not HEX_COLOR.match(
            cell.get("color", "")
        ):
            errors.append(f"cells.{key}.color must be #RRGGBB")
        dynamic = cell.get("dynamic")
        if dynamic is not None:
            if not isinstance(dynamic, dict):
                errors.append(f"cells.{key}.dynamic must be an object")
            else:
                dynamic_type = dynamic.get("type", "cycle")
                if dynamic_type not in ("cycle", "delayed_trap"):
                    errors.append(
                        f"cells.{key}.dynamic.type must be cycle or delayed_trap"
                    )
                if dynamic_type == "cycle":
                    if not isinstance(dynamic.get("intervalSeconds"), (int, float)) or dynamic.get("intervalSeconds", 0) <= 0:
                        errors.append(
                            f"cells.{key}.dynamic.intervalSeconds must be positive"
                        )
                    pattern = dynamic.get("pattern")
                    if not isinstance(pattern, list) or len(pattern) < 2:
                        errors.append(
                            f"cells.{key}.dynamic.pattern needs at least 2 states"
                        )

    params = package.get("modeParams")
    if not isinstance(params, dict):
        errors.append("modeParams must be an object")
        params = {}
    if mode == "survival_lava":
        for key in ("survivalSeconds", "dwellSeconds", "warnSeconds"):
            _positive_number(params, key, errors)
        _positive_number(params, "pointsPerTile", errors, allow_zero=True)
        _positive_number(params, "pitConfirmSeconds", errors)
    elif mode == "hex_fall":
        for key in ("survivalSeconds", "pitConfirmSeconds", "collapseWarnSeconds"):
            _positive_number(params, key, errors)
        for key in ("collapseEverySeconds", "collapseCount"):
            _positive_number(params, key, errors)
    elif mode == "target_hunt":
        for key in (
            "startingSeconds", "targetBonusSeconds", "targetConfirmSeconds",
            "pointsPerTarget",
        ):
            _positive_number(params, key, errors)
        for key in ("spawnPitCount", "spawnWallCount"):
            _positive_number(params, key, errors, allow_zero=True)

    if errors and raise_on_error:
        raise PackageValidationError(errors)
    return errors


def package_from_manifest(level_id: str, path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    item = next(
        (entry for entry in manifest.get("levels", []) if entry.get("id") == level_id),
        None,
    )
    if item is None:
        raise ValueError(f"unknown level id: {level_id}")
    cells = json.loads((ROOT / item["map"]).read_text(encoding="utf-8"))
    mode = item.get("mode") or "reach_end"
    params = deepcopy(MODE_DEFAULTS.get(mode, {}))
    params.update(item.get("modeParams", {}))
    if mode == "survival_lava" and not item.get("modeParams"):
        for key in MODE_DEFAULTS[mode]:
            if key in item:
                params[key] = item[key]
    package = {
        "version": PACKAGE_VERSION,
        "seed": int(item.get("seed", 1)),
        "meta": {
            key: deepcopy(item[key])
            for key in (
                "id", "number", "title", "subtitle", "timeLimitSeconds",
                "startCell", "endCell", "feature", "rules", "kenLine", "trollLine",
            )
        },
        "mode": mode,
        "modeParams": params,
        "cells": cells,
    }
    validate_package(package)
    return package


def compile_package(package: dict[str, Any], map_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_package(package)
    meta = deepcopy(package["meta"])
    entry = {
        **meta,
        "map": map_path,
        "mode": package["mode"] if package["mode"] != "reach_end" else None,
        "modeParams": deepcopy(package["modeParams"]),
        "seed": package["seed"],
    }
    if entry["mode"] is None:
        entry.pop("mode")
    return entry, deepcopy(package["cells"])


def install_package(
    package: dict[str, Any],
    *,
    manifest_path: Path = MANIFEST_PATH,
    maps_dir: Path | None = None,
) -> tuple[Path, Path]:
    validate_package(package)
    maps_dir = maps_dir or ROOT / "maps"
    level_id = package["meta"]["id"]
    map_path = maps_dir / f"{level_id}.json"
    try:
        relative_map = str(map_path.relative_to(ROOT))
    except ValueError:
        relative_map = str(map_path)
    entry, cells = compile_package(package, relative_map)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    levels = manifest.setdefault("levels", [])
    existing = next(
        (index for index, item in enumerate(levels) if item.get("id") == level_id),
        None,
    )
    if existing is None:
        levels.append(entry)
    else:
        levels[existing] = entry
    levels.sort(key=lambda item: int(item["number"]))
    maps_dir.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(cells, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path, map_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("level_id")
    export.add_argument("output", type=Path)
    check = sub.add_parser("validate")
    check.add_argument("package", type=Path)
    install = sub.add_parser("install")
    install.add_argument("package", type=Path)
    args = parser.parse_args(argv)

    if args.command == "export":
        package = package_from_manifest(args.level_id)
        args.output.write_text(json.dumps(package, indent=2) + "\n")
        print(args.output)
    else:
        package = json.loads(args.package.read_text(encoding="utf-8"))
        validate_package(package)
        if args.command == "install":
            print(*install_package(package))
        else:
            print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
