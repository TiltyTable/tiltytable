#!/usr/bin/env python3
"""Serve a small web monitor for the USB trackball's motion and buttons."""

from __future__ import annotations

import argparse
import json
import os
import select
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_DEVICE = Path("/dev/input/by-id/usb-13ba_Barcode_Reader-if01-event-mouse")
EVENT_ROOT = Path("/dev/input")
EVENT = struct.Struct("llHHI")
EV_KEY = 0x01
EV_REL = 0x02
BUTTONS = {0x110: "BTN_LEFT", 0x111: "BTN_RIGHT", 0x112: "BTN_MIDDLE", 0x113: "BTN_SIDE", 0x114: "BTN_EXTRA"}
REL_NAMES = {0x00: "REL_X", 0x01: "REL_Y", 0x08: "REL_WHEEL"}


def signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value


class TrackballState:
    def __init__(self, requested: str | None):
        self.requested = Path(requested) if requested else None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.fd: int | None = None
        self.thread: threading.Thread | None = None
        self.status = "starting"
        self.error = ""
        self.device = ""
        self.total_x = 0
        self.total_y = 0
        self.buttons = {code: {"code": code, "name": name, "pressed": False, "presses": 0} for code, name in BUTTONS.items()}
        self.events: list[dict[str, str]] = []

    def find_device(self) -> Path | None:
        if self.requested:
            return self.requested
        if DEFAULT_DEVICE.exists():
            return DEFAULT_DEVICE
        by_id = EVENT_ROOT / "by-id"
        if by_id.exists():
            for path in sorted(by_id.iterdir()):
                if "mouse" in path.name.lower():
                    return path
        return None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="trackball-input", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        self.close_fd()

    def close_fd(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def _run(self) -> None:
        path = self.find_device()
        if path is None:
            with self.lock:
                self.status, self.error = "missing", "No mouse-like /dev/input device found"
            return
        try:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            with self.lock:
                self.device, self.status, self.error = str(path), "connected", ""
            while not self.stop_event.is_set():
                if not select.select([self.fd], [], [], 0.25)[0]:
                    continue
                try:
                    data = os.read(self.fd, EVENT.size * 128)
                except BlockingIOError:
                    continue
                self.process(data)
        except PermissionError as exc:
            with self.lock:
                self.status, self.error = "permission", f"Cannot read {path}: {exc}"
        except OSError as exc:
            with self.lock:
                self.status, self.error = "error", str(exc)
        finally:
            self.close_fd()

    def process(self, data: bytes) -> None:
        for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
            sec, usec, event_type, code, raw = EVENT.unpack_from(data, offset)
            value = signed32(raw)
            with self.lock:
                if event_type == EV_REL:
                    if code == 0:
                        self.total_x += value
                    elif code == 1:
                        self.total_y += value
                    message = f"{REL_NAMES.get(code, f'REL_{code}')} {value:+d}"
                elif event_type == EV_KEY:
                    button = self.buttons.setdefault(code, {"code": code, "name": f"KEY/BTN 0x{code:x}", "pressed": False, "presses": 0})
                    button["pressed"] = value == 1
                    if value == 1:
                        button["presses"] += 1
                    message = f"{button['name']} {'pressed' if value == 1 else 'released'}"
                else:
                    continue
                self.events.insert(0, {"time": f"{sec}.{usec:06d}", "message": message})
                del self.events[100:]

    def snapshot(self) -> dict:
        with self.lock:
            return {"status": self.status, "error": self.error, "device": self.device, "total_x": self.total_x, "total_y": self.total_y, "buttons": list(self.buttons.values()), "events": list(self.events)}


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trackball Input</title><link rel="stylesheet" href="/styles.css"></head><body><main><header><p> TILTYTABLE / INPUT DIAGNOSTICS</p><h1>Trackball Input</h1><span id="status">starting</span></header><section class="device" id="device">Searching for device…</section><section class="motion"><article><small>X TOTAL</small><strong id="x">0</strong></article><article><small>Y TOTAL</small><strong id="y">0</strong></article></section><section><h2>Buttons</h2><div id="buttons"></div></section><section><h2>Event history</h2><pre id="events">No input received yet.</pre></section></main><script src="/app.js"></script></body></html>'''

JS = r'''const $=id=>document.getElementById(id);async function update(){try{const s=await fetch('/api/state',{cache:'no-store'}).then(r=>r.json());$('status').textContent=s.status;$('status').className=s.status==='connected'?'ok':'bad';$('device').textContent=s.error||s.device||'Searching for device…';$('x').textContent=s.total_x;$('y').textContent=s.total_y;$('buttons').innerHTML=s.buttons.map(b=>`<div class="button ${b.pressed?'down':''}"><b>${b.name}</b><span>${b.pressed?'PRESSED':'released'} · ${b.presses} presses</span></div>`).join('');$('events').textContent=s.events.length?s.events.slice(0,20).map(e=>`${e.time}  ${e.message}`).join('\n'):'No input received yet.'}catch(e){$('status').textContent='server offline';$('status').className='bad'}}setInterval(update,100);update();'''

CSS = r''':root{color-scheme:dark;--bg:#08110d;--panel:#112018;--line:#204331;--text:#c5d9ca;--muted:#6e8c78;--green:#00d98a;--red:#f05050}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#153422,var(--bg) 55%);color:var(--text);font:15px system-ui,sans-serif}main{width:min(760px,calc(100% - 32px));margin:40px auto}header{position:relative;border-bottom:1px solid var(--line);padding-bottom:18px}header p,h2,small,pre,.device{font-family:ui-monospace,monospace}header p{color:var(--green);font-size:11px;letter-spacing:.16em}h1{font:42px Georgia,serif;margin:4px 0}header span{position:absolute;right:0;bottom:20px;color:var(--muted);font-family:monospace;text-transform:uppercase}header span.ok{color:var(--green)}header span.bad{color:var(--red)}.device{margin:22px 0;color:var(--muted);overflow-wrap:anywhere}.motion{display:grid;grid-template-columns:1fr 1fr;gap:2px;background:var(--line);margin-bottom:28px}.motion article{background:var(--panel);padding:20px}.motion small{display:block;color:var(--muted);letter-spacing:.12em}.motion strong{display:block;color:var(--green);font:32px monospace;margin-top:8px}h2{font-size:12px;letter-spacing:.16em;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:8px}.button{display:flex;justify-content:space-between;background:var(--panel);border:1px solid var(--line);padding:12px;margin:5px 0;font-family:monospace}.button span{color:var(--muted)}.button.down{background:#0b4b32;border-color:var(--green);color:#fff}.button.down span{color:var(--green)}pre{background:#050a07;border:1px solid var(--line);padding:14px;min-height:180px;max-height:300px;overflow:auto;color:var(--muted);line-height:1.6}'''


class Handler(BaseHTTPRequestHandler):
    state: TrackballState

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_text(HTML, "text/html; charset=utf-8")
        elif path == "/app.js":
            self.send_text(JS, "application/javascript; charset=utf-8")
        elif path == "/styles.css":
            self.send_text(CSS, "text/css; charset=utf-8")
        elif path == "/api/state":
            self.send_text(json.dumps(self.state.snapshot()), "application/json")
        else:
            self.send_error(404)

    def send_text(self, text: str, content_type: str):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--device", default=None, help="Override the /dev/input event device")
    args = parser.parse_args()
    state = TrackballState(args.device)
    state.start()
    Handler.state = state
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Trackball input web monitor: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
