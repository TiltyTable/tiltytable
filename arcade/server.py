from __future__ import annotations

import argparse
import atexit
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, make_response, request, send_from_directory

from .ball_adapters import HttpKinectBallAdapter, ManualBallAdapter
from .engine import GameEngine
from .hardware import BaseTableHardware, ModuleGridHardware, SimulatedTableHardware
from .levels import load_levels
from .storage import ScoreStore

STATIC_DIR = Path(__file__).with_name("static")
EDITOR_DIR = Path(__file__).with_name("editor")


def create_app(
    *,
    hardware: BaseTableHardware | None = None,
    score_path: Path | None = None,
    start_ticker: bool = True,
    ball_adapter: ManualBallAdapter | HttpKinectBallAdapter | None = None,
) -> Flask:
    app = Flask(__name__, static_folder=None)
    table = hardware or SimulatedTableHardware()
    scores = ScoreStore(score_path) if score_path else ScoreStore()
    tracking = ball_adapter or ManualBallAdapter()
    engine = GameEngine(load_levels(), table, scores, ball_adapter=tracking)
    app.config["GAME_ENGINE"] = engine

    stop_event = threading.Event()

    def ticker() -> None:
        while not stop_event.wait(0.1):
            try:
                engine.tick()
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

    @app.get("/styles.css")
    def styles_css():
        return send_from_directory(STATIC_DIR, "styles.css")

    @app.get("/PressStart2P-Regular.ttf")
    def pixel_font():
        return send_from_directory(STATIC_DIR, "PressStart2P-Regular.ttf")

    @app.get("/editor")
    @app.get("/editor/")
    def editor_index():
        return send_from_directory(EDITOR_DIR, "index.html")

    @app.get("/editor/<path:filename>")
    def editor_asset(filename: str):
        return send_from_directory(EDITOR_DIR, filename)

    @app.get("/api/state")
    def state():
        return jsonify({"ok": True, "game": engine.public_state()})

    @app.post("/api/action")
    def action():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        name = str(body.get("action", ""))
        try:
            if name == "setup":
                engine.setup()
            elif name == "start-gauntlet":
                engine.start_gauntlet()
            elif name == "set-initials":
                engine.set_initials(str(body.get("initials", "")))
            elif name == "show-level-select":
                engine.show_level_select()
            elif name == "select-level":
                engine.select_practice_level(str(body.get("levelId", "")))
            elif name == "continue":
                engine.continue_action()
            elif name == "confirm-placement":
                engine.confirm_placement()
            elif name == "restart":
                engine.restart()
            elif name == "complete":
                engine.complete_level()
            elif name == "abandon":
                engine.abandon()
            else:
                return jsonify({"ok": False, "error": f"unknown action: {name}"}), 400
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
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
    parser.add_argument(
        "--kinect-url",
        default="",
        help="Kinect web control base URL (e.g. http://127.0.0.1:8080) for survival ball tracking",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    hardware: BaseTableHardware
    if args.hardware:
        hardware = ModuleGridHardware(port=args.module_port)
    else:
        hardware = SimulatedTableHardware()

    ball_adapter: ManualBallAdapter | HttpKinectBallAdapter = ManualBallAdapter()
    if args.kinect_url:
        ball_adapter = HttpKinectBallAdapter(args.kinect_url)

    app = create_app(hardware=hardware, ball_adapter=ball_adapter)
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

