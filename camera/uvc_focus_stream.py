#!/usr/bin/env python3
"""Live MJPEG preview for UVC webcams (Arducam / Microdia / etc.).

Open http://<jetson-ip>:8091/ in a browser on the same LAN.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


class UvcStream:
    def __init__(
        self,
        device: int = 0,
        width: int = 1280,
        height: int = 720,
        jpeg_quality: int = 80,
    ) -> None:
        self.device = device
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._shape = (0, 0)
        self._fps = 0.0
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None
        self.brightness = 0.5
        self.contrast = 0.5
        self.auto_exposure = True

    def start(self) -> None:
        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open /dev/video{self.device}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)  # auto on many UVC cams
        self._cap = cap
        self._thread = threading.Thread(target=self._run, name="uvc-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def set_brightness(self, value: float) -> None:
        self.brightness = max(0.0, min(1.0, float(value)))
        if self._cap is not None:
            self._cap.set(cv2.CAP_PROP_BRIGHTNESS, self.brightness)

    def set_auto_exposure(self, enabled: bool) -> None:
        self.auto_exposure = bool(enabled)
        if self._cap is not None:
            # V4L2 quirks: 3=auto, 1=manual on many cams
            self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3 if self.auto_exposure else 1)

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def status(self) -> dict:
        with self._lock:
            return {
                "device": self.device,
                "shape": self._shape,
                "fps": round(self._fps, 1),
                "brightness": self.brightness,
                "auto_exposure": self.auto_exposure,
                "error": self._error,
            }

    def _run(self) -> None:
        assert self._cap is not None
        last = time.time()
        frames = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                with self._lock:
                    self._error = "read failed"
                time.sleep(0.05)
                continue
            vis = frame
            h, w = vis.shape[:2]
            cx, cy = w // 2, h // 2
            cv2.line(vis, (cx - 40, cy), (cx + 40, cy), (0, 255, 0), 1)
            cv2.line(vis, (cx, cy - 40), (cx, cy + 40), (0, 255, 0), 1)
            cv2.rectangle(vis, (cx - 80, cy - 80), (cx + 80, cy + 80), (0, 255, 0), 1)
            mean = float(frame.mean())
            label = f"UVC /dev/video{self.device}  mean={mean:.0f}  {w}x{h}"
            cv2.putText(vis, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            ok_enc, enc = cv2.imencode(
                ".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
            )
            if ok_enc:
                with self._lock:
                    self._jpeg = enc.tobytes()
                    self._shape = (h, w)
                    self._error = None
            frames += 1
            now = time.time()
            if now - last >= 1.0:
                self._fps = frames / (now - last)
                frames = 0
                last = now


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>TiltyTable UVC focus</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
         background: #111; color: #eee; }}
  header {{ padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 12px;
           align-items: center; background: #1a1a1a; border-bottom: 1px solid #333; }}
  h1 {{ font-size: 1rem; margin: 0; font-weight: 600; }}
  .meta {{ opacity: 0.75; font-size: 0.85rem; }}
  form {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  input[type=number] {{ width: 5rem; padding: 6px 8px; border-radius: 6px;
                       border: 1px solid #444; background: #222; color: #eee; }}
  button, .chip {{ padding: 6px 12px; border-radius: 6px; border: 1px solid #456;
                  background: #2a3a2a; color: #cfc; cursor: pointer; text-decoration: none; }}
  button:hover, .chip:hover {{ background: #3a5a3a; }}
  main {{ padding: 12px; }}
  img {{ max-width: 100%; height: auto; background: #000; display: block;
        margin: 0 auto; border: 1px solid #333; }}
  .hint {{ margin-top: 10px; opacity: 0.7; font-size: 0.9rem; max-width: 60rem; }}
</style>
</head>
<body>
<header>
  <h1>Arducam / UVC live focus</h1>
  <span class="meta" id="status">…</span>
  <form action="/set" method="get">
    <label>Brightness 0–1
      <input type="number" name="brightness" id="bri" min="0" max="1" step="0.05" value="{brightness}"/>
    </label>
    <button type="submit">Apply</button>
  </form>
  <a class="chip" href="/set?auto_exposure=1">Auto exp</a>
  <a class="chip" href="/set?auto_exposure=0">Manual exp</a>
</header>
<main>
  <img src="/stream" alt="live UVC stream"/>
  <p class="hint">
    Aim the camera at the table and turn the focus ring while watching this page.
    Green crosshair marks frame center. MindVision preview (if running) is on port 8090.
  </p>
</main>
<script>
async function refresh() {{
  try {{
    const r = await fetch('/status');
    const j = await r.json();
    document.getElementById('status').textContent =
      `video${{j.device}}  ${{j.shape[1]}}×${{j.shape[0]}}  ${{j.fps}} fps` +
      (j.error ? `  ERR ${{j.error}}` : '');
    document.getElementById('bri').value = j.brightness;
  }} catch (e) {{}}
}}
setInterval(refresh, 1000); refresh();
</script>
</body>
</html>
"""


def make_handler(stream: UvcStream):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                body = PAGE.format(brightness=stream.brightness).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/status":
                body = json.dumps(stream.status()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/set":
                if "brightness" in qs:
                    stream.set_brightness(float(qs["brightness"][0]))
                if "auto_exposure" in qs:
                    stream.set_auto_exposure(qs["auto_exposure"][0] not in ("0", "false", "False"))
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if parsed.path == "/stream":
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header(
                    "Content-Type", "multipart/x-mixed-replace; boundary=frame"
                )
                self.end_headers()
                try:
                    while True:
                        jpeg = stream.latest_jpeg()
                        if jpeg:
                            self.wfile.write(
                                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                                + jpeg
                                + b"\r\n"
                            )
                            self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    return
            self.send_error(404)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--device", type=int, default=0, help="V4L2 index (/dev/videoN)")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    args = parser.parse_args()

    stream = UvcStream(
        device=args.device,
        width=args.width,
        height=args.height,
        jpeg_quality=args.jpeg_quality,
    )
    stream.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(stream))
    print(f"UVC focus preview: http://{args.host}:{args.port}/  (use Jetson LAN IP)")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        server.server_close()
        stream.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
