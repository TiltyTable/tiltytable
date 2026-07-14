#!/usr/bin/env python3
"""LED palette + per-tile RGB gain resolution.

Ideal named colors live in led_palette.json. Per-tile multiplicative gains
live in led_color_cal.json so a single named color (e.g. trap) looks
consistent across diffuser tiles.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

CAL_DIR = os.path.dirname(os.path.abspath(__file__))
PALETTE_PATH = os.path.join(CAL_DIR, "led_palette.json")
GAINS_PATH = os.path.join(CAL_DIR, "led_color_cal.json")

RGB = Tuple[int, int, int]
Gains = Dict[str, Dict[str, float]]


def _clamp255(x: float) -> int:
    return max(0, min(255, int(round(x))))


def hex_to_rgb(color: str) -> RGB:
    c = color.strip().lstrip("#")
    if len(c) != 6:
        raise ValueError(f"bad color {color!r}")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def cell_key(row: int, col: int) -> str:
    return f"{row},{col}"


def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path) as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_palette(path: str = PALETTE_PATH) -> dict:
    data = load_json(path, {"colors": {}, "export_hex_aliases": {}})
    data.setdefault("colors", {})
    data.setdefault("export_hex_aliases", {})
    # Normalize: ensure rgb present from hex
    for name, entry in data["colors"].items():
        if "rgb" not in entry and "hex" in entry:
            entry["rgb"] = list(hex_to_rgb(entry["hex"]))
        elif "hex" not in entry and "rgb" in entry:
            entry["hex"] = rgb_to_hex(tuple(entry["rgb"]))  # type: ignore[arg-type]
    return data


def load_cal(path: str = GAINS_PATH) -> dict:
    data = load_json(path, {"reference_cell": "0,0", "gains": {}, "color_overrides": {}})
    data.setdefault("reference_cell", "0,0")
    data.setdefault("gains", {})
    data.setdefault("color_overrides", {})
    return data


def save_palette(palette: dict, path: str = PALETTE_PATH) -> None:
    save_json(path, palette)


def save_cal(cal: dict, path: str = GAINS_PATH) -> None:
    save_json(path, cal)


def get_gains(cal: dict, row: int, col: int) -> Tuple[float, float, float]:
    g = cal.get("gains", {}).get(cell_key(row, col))
    if not g:
        return 1.0, 1.0, 1.0
    return float(g.get("r", 1.0)), float(g.get("g", 1.0)), float(g.get("b", 1.0))


def get_brightness(cal: dict, row: int, col: int) -> float:
    """Return a tile's whole-colour brightness multiplier."""
    g = cal.get("gains", {}).get(cell_key(row, col))
    return float(g.get("brightness", 1.0)) if g else 1.0


def set_gains(cal: dict, row: int, col: int, r: float, g: float, b: float) -> None:
    previous = cal.setdefault("gains", {}).get(cell_key(row, col), {})
    cal.setdefault("gains", {})[cell_key(row, col)] = {
        "r": round(r, 4),
        "g": round(g, 4),
        "b": round(b, 4),
        "brightness": round(float(previous.get("brightness", 1.0)), 4),
    }


def set_brightness(cal: dict, row: int, col: int, brightness: float) -> None:
    """Set whole-colour brightness without changing the RGB balance."""
    entry = cal.setdefault("gains", {}).setdefault(cell_key(row, col), {})
    entry["brightness"] = round(brightness, 4)


def get_color_override(cal: dict, name: str, row: int, col: int) -> Optional[RGB]:
    """Return a direct per-colour RGB override, if this tile has one."""
    entry = cal.get("color_overrides", {}).get(name, {}).get(cell_key(row, col))
    if not entry:
        return None
    return int(entry["r"]), int(entry["g"]), int(entry["b"])


def set_color_override(cal: dict, name: str, row: int, col: int, rgb: RGB) -> None:
    """Store an exact RGB output for one palette colour on one tile."""
    r, g, b = rgb
    cal.setdefault("color_overrides", {}).setdefault(name, {})[cell_key(row, col)] = {
        "r": max(0, min(255, int(round(r)))),
        "g": max(0, min(255, int(round(g)))),
        "b": max(0, min(255, int(round(b)))),
    }


def clear_color_override(cal: dict, name: str, row: int, col: int) -> None:
    """Return one tile/colour to its palette RGB value."""
    overrides = cal.get("color_overrides", {})
    entries = overrides.get(name)
    if not entries:
        return
    entries.pop(cell_key(row, col), None)
    if not entries:
        overrides.pop(name, None)


def apply_gains(rgb: RGB, gains: Tuple[float, float, float], dim: float = 1.0) -> RGB:
    sr, sg, sb = gains
    return (
        _clamp255(rgb[0] * sr * dim),
        _clamp255(rgb[1] * sg * dim),
        _clamp255(rgb[2] * sb * dim),
    )


def ideal_rgb(palette: dict, name: str) -> RGB:
    colors = palette.get("colors", {})
    if name not in colors:
        raise KeyError(f"unknown palette color {name!r}; have {sorted(colors)}")
    entry = colors[name]
    if "rgb" in entry:
        r, g, b = entry["rgb"]
        return int(r), int(g), int(b)
    return hex_to_rgb(entry["hex"])


def calibration_name(palette: dict, name: str) -> str:
    """Return the visible colour name used as the per-tile override key."""
    entry = palette.get("colors", {}).get(name, {})
    return str(entry.get("calibration_name", name))


def resolve_name(palette: dict, cal: dict, name: str, row: int, col: int,
                 dim: float = 1.0) -> RGB:
    override = get_color_override(cal, calibration_name(palette, name), row, col)
    if override is not None:
        # Direct overrides are intentionally not passed through legacy gains:
        # the calibration GUI's sliders represent the actual output RGB.
        return apply_gains(override, (1.0, 1.0, 1.0), dim)
    return apply_gains(
        ideal_rgb(palette, name), get_gains(cal, row, col),
        dim * get_brightness(cal, row, col),
    )


def resolve_rgb(cal: dict, rgb: RGB, row: int, col: int, dim: float = 1.0) -> RGB:
    return apply_gains(rgb, get_gains(cal, row, col), dim * get_brightness(cal, row, col))


def nearest_palette_name(palette: dict, rgb: RGB) -> Optional[str]:
    """Map an arbitrary RGB to the closest named palette color (Euclidean)."""
    best_name, best_d = None, None
    for name in palette.get("colors", {}):
        ir, ig, ib = ideal_rgb(palette, name)
        d = (ir - rgb[0]) ** 2 + (ig - rgb[1]) ** 2 + (ib - rgb[2]) ** 2
        if best_d is None or d < best_d:
            best_d, best_name = d, name
    return best_name


def resolve_hex_or_name(palette: dict, cal: dict, color: str, row: int, col: int,
                        dim: float = 1.0, use_aliases: bool = True) -> RGB:
    """Resolve a palette name or #RRGGBB (via alias or nearest) through gains."""
    key = color.strip()
    colors = palette.get("colors", {})
    if key in colors:
        return resolve_name(palette, cal, key, row, col, dim)

    hex_u = key if key.startswith("#") else f"#{key}"
    hex_u = hex_u.upper()
    # Normalize #rrggbb
    try:
        rgb = hex_to_rgb(hex_u)
        hex_norm = rgb_to_hex(rgb)
    except ValueError:
        raise ValueError(f"not a palette name or hex color: {color!r}")

    if use_aliases:
        aliases = {k.upper(): v for k, v in palette.get("export_hex_aliases", {}).items()}
        if hex_norm in aliases:
            return resolve_name(palette, cal, aliases[hex_norm], row, col, dim)
        # also try lowercase keys as stored
        aliases_raw = palette.get("export_hex_aliases", {})
        for ak, av in aliases_raw.items():
            if ak.upper() == hex_norm:
                return resolve_name(palette, cal, av, row, col, dim)

    # Prefer the nearest named palette color before applying calibration.
    name = nearest_palette_name(palette, rgb)
    if name is not None:
        return resolve_name(palette, cal, name, row, col, dim)
    return resolve_rgb(cal, rgb, row, col, dim)


def palette_names(palette: dict):
    return list(palette.get("colors", {}).keys())
