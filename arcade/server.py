from __future__ import annotations

import argparse
import atexit
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, make_response, request, send_from_directory

from .ball_adapters import InProcessKinectBallAdapter, ManualBallAdapter
from .engine import GameEngine
from .hardware import BaseTableHardware, ModuleGridHardware, SimulatedTableHardware
from .integrations import BallTrackingAdapter, TiltControlAdapter
from .levels import load_levels
from .maze_editor import MazeValidationError, load_maze, save_maze
from .stewart_tilt import StewartTiltService
from .storage import ScoreStore

STATIC_DIR = Path(__file__).with_name("static")
EDITOR_DIR = Path(__file__).with_name("editor")
DEFAULT_ARCADE_CONFIG = Path(__file__).with_name("config.json")
DEFAULT_MAZE_MAP = Path(__file__).resolve().parents[1] / "maps" / "arcade-level-4.json"


def load_game_tick_ms(config_path: Path = DEFAULT_ARCADE_CONFIG) -> int:
    import json

    with Path(config_path).open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    value = config.get("tracking", {}).get("game_tick_ms")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("arcade tracking.game_tick_ms must be a positive integer")
    return value


def create_app(
    *,
    hardware: BaseTableHardware | None = None,
    score_path: Path | None = None,
    start_ticker: bool = True,
    auto_setup: bool = False,
    ball_adapter: BallTrackingAdapter | None = None,
    tilt_adapter: TiltControlAdapter | None = None,
    game_tick_ms: int | None = None,
    editor_map_path: Path | None = None,
) -> Flask:
    if game_tick_ms is not None and (
        isinstance(game_tick_ms, bool)
        or not isinstance(game_tick_ms, int)
        or game_tick_ms <= 0
    ):
        raise ValueError("game_tick_ms must be a positive integer")
    app = Flask(__name__, static_folder=None)
    table = hardware or SimulatedTableHardware()
    scores = ScoreStore(score_path) if score_path else ScoreStore()
    tracking = ball_adapter or ManualBallAdapter()
    engine = GameEngine(
        load_levels(),
        table,
        scores,
        ball_adapter=tracking,
        tilt_adapter=tilt_adapter,
    )
    app.config["GAME_ENGINE"] = engine
    maze_map_path = Path(editor_map_path or DEFAULT_MAZE_MAP)

    stop_event = threading.Event()
    tick_interval_s = (
        (game_tick_ms if game_tick_ms is not None else load_game_tick_ms()) / 1000.0
    )
    tracking.start()
    try:
        if tilt_adapter is not None:
            tilt_adapter.start()
    except Exception:
        tracking.stop()
        raise
    if auto_setup:
        engine.setup()

    def sync_tilt() -> None:
        if tilt_adapter is not None:
            tilt_adapter.set_active(engine.tilt_requested())

    def ticker() -> None:
        wait_for_frame = getattr(tracking, "wait_for_frame", None)
        while not stop_event.is_set():
            if callable(wait_for_frame):
                wait_for_frame(tick_interval_s)
                if stop_event.is_set():
                    break
            elif stop_event.wait(tick_interval_s):
                break
            try:
                engine.tick()
                sync_tilt()
            except Exception:
                # The next state response exposes hardware errors. Keep the
                # server alive so the operator can recover or abandon.
                time.sleep(0.2)

    if start_ticker:
        threading.Thread(target=ticker, name="arcade-engine-ticker", daemon=True).start()

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/app.js")
    def app_js():
        return send_from_directory(STATIC_DIR, "app.js")

    @app.get("/ui_logic.js")
    def ui_logic_js():
        return send_from_directory(STATIC_DIR, "ui_logic.js")

    @app.get("/styles.css")
    def styles_css():
        return send_from_directory(STATIC_DIR, "styles.css")

    @app.get("/PressStart2P-Regular.ttf")
    def pixel_font():
        return send_from_directory(STATIC_DIR, "PressStart2P-Regular.ttf")

    @app.get("/editor")
    @app.get("/editor/")
    def maze_editor():
        return send_from_directory(EDITOR_DIR, "index.html")

    @app.get("/editor/<path:filename>")
    def maze_editor_asset(filename: str):
        return send_from_directory(EDITOR_DIR, filename)

    @app.get("/api/editor/maze")
    def editor_load_maze():
        try:
            cells = load_maze(maze_map_path)
        except (OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({
            "ok": True,
            "map": maze_map_path.name,
            "cells": cells,
        })

    @app.post("/api/editor/maze")
    def editor_save_maze():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            cells = save_maze(maze_map_path, body.get("cells"))
        except MazeValidationError as exc:
            return jsonify({"ok": False, "error": str(exc), "errors": exc.errors}), 400
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({
            "ok": True,
            "map": maze_map_path.name,
            "cells": cells,
        })

    @app.get("/api/state")
    def state():
        return jsonify({"ok": True, "game": engine.public_state()})

    @app.get("/api/ball")
    def ball_state():
        return jsonify({
            "ok": True,
            "ball": engine.live_ball_state(),
            "trackingEnabled": bool(
                engine.ball_adapter is not None
                and getattr(engine.ball_adapter, "is_live", False)
            ),
        })

    @app.post("/api/action")
    def action():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        name = str(body.get("action", ""))
        try:
            if name == "setup":
                engine.setup()
            elif name == "show-level-select":
                engine.show_level_select()
            elif name == "select-level":
                engine.select_practice_level(str(body.get("levelId", "")))
            elif name == "set-initials":
                engine.set_initials(str(body.get("initials", "")))
            elif name == "continue":
                engine.continue_action()
            elif name == "confirm-placement":
                engine.confirm_placement()
            elif name == "restart":
                engine.restart()
            elif name == "retry":
                engine.retry_level()
            elif name == "unstick":
                engine.unstick()
            elif name == "complete":
                engine.complete_level()
            elif name == "abandon":
                engine.abandon()
            else:
                return jsonify({"ok": False, "error": f"unknown action: {name}"}), 400
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        sync_tilt()
        return jsonify({"ok": True, "game": engine.public_state()})

    @app.post("/api/leaderboard/reset")
    def reset_leaderboard():
        scores.clear()
        return jsonify({"ok": True, "game": engine.public_state()})

    @app.post("/api/dev/ball-cell")
    def dev_ball_cell():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        key = body.get("key")
        if not key and "row" in body and "col" in body:
            from .ball_adapters import row_col_to_cell_key

            key = row_col_to_cell_key(int(body["row"]), int(body["col"]))
        try:
            engine.set_ball_cell(str(key) if key else None)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "game": engine.public_state()})

    @app.get("/api/leaderboard/export")
    def export_leaderboard():
        response = make_response(
            jsonify({"version": 1, "scores": scores.all()}).get_data()
        )
        response.headers["Content-Type"] = "application/json"
        response.headers["Content-Disposition"] = (
            'attachment; filename="tiltytable-scores.json"'
        )
        return response

    def shutdown() -> None:
        stop_event.set()
        if tilt_adapter is not None:
            try:
                tilt_adapter.stop()
            except Exception:
                pass
        try:
            tracking.stop()
        except Exception:
            pass
        try:
            table.shutdown()
        except Exception:
            pass

    app.config["ARCADE_SHUTDOWN"] = shutdown
    atexit.register(shutdown)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TiltyTable 480p arcade server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--hardware", action="store_true", help="connect live module grid")
    parser.add_argument("--module-port", default="/dev/arduino-modules")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    hardware: BaseTableHardware
    if args.hardware:
        hardware = ModuleGridHardware(port=args.module_port)
    else:
        hardware = SimulatedTableHardware()

    ball_adapter: BallTrackingAdapter = (
        InProcessKinectBallAdapter(args.config)
        if args.hardware
        else ManualBallAdapter()
    )

    tilt_adapter: TiltControlAdapter | None = (
        StewartTiltService() if args.hardware else None
    )

    app = create_app(
        hardware=hardware,
        ball_adapter=ball_adapter,
        tilt_adapter=tilt_adapter,
        auto_setup=True,
    )
    url = f"http://{args.host}:{args.port}"
    mode = "LIVE MODULE GRID" if args.hardware else "SIMULATION"
    print(f"TiltyTable Arcade ({mode}) — {url}")
    if args.debug:
        app.run(host=args.host, port=args.port, debug=True, threaded=True, use_reloader=False)
    else:
        from waitress import serve

        serve(app, host=args.host, port=args.port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
