#!/usr/bin/env python3
"""Live MJPEG preview for MindVision focus / exposure adjustment.

Open http://<jetson-ip>:8090/ in a browser on the same LAN.

Query params (also buttons on the page):
  /stream?exposure_ms=200
  /set?exposure_ms=100   (redirects back to /)
"""

from __future__ import annotations

import argparse
import platform
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

_CAMERA_DIR = Path(__file__).resolve().parent
if str(_CAMERA_DIR) not in sys.path:
    sys.path.insert(0, str(_CAMERA_DIR))

import mvsdk  # noqa: E402


class MindVisionStream:
    def __init__(self, exposure_ms: float = 200.0, jpeg_quality: int = 80) -> None:
        self.exposure_ms = float(exposure_ms)
        self.jpeg_quality = int(jpeg_quality)
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._shape = (0, 0)
        self._fps = 0.0
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._h_camera = None
        self._p_frame = None
        self._mono = True

    def start(self) -> None:
        devices = mvsdk.CameraEnumerateDevice()
        if not devices:
            raise RuntimeError("No MindVision camera found")
        info = devices[0]
        self._h_camera = mvsdk.CameraInit(info, -1, -1)
        cap = mvsdk.CameraGetCapability(self._h_camera)
        self._mono = cap.sIspCapacity.bMonoSensor != 0
        fmt = mvsdk.CAMERA_MEDIA_TYPE_MONO8 if self._mono else mvsdk.CAMERA_MEDIA_TYPE_BGR8
        mvsdk.CameraSetIspOutFormat(self._h_camera, fmt)
        mvsdk.CameraSetTriggerMode(self._h_camera, 0)
        mvsdk.CameraSetAeState(self._h_camera, 0)
        mvsdk.CameraSetExposureTime(self._h_camera, int(self.exposure_ms * 1000))
        mvsdk.CameraPlay(self._h_camera)
        frame_bytes = (
            cap.sResolutionRange.iWidthMax
            * cap.sResolutionRange.iHeightMax
            * (1 if self._mono else 3)
        )
        self._p_frame = mvsdk.CameraAlignMalloc(frame_bytes, 16)
        self._thread = threading.Thread(target=self._run, name="mv-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._h_camera is not None:
            try:
                mvsdk.CameraUnInit(self._h_camera)
            except Exception:
                pass
            self._h_camera = None
        if self._p_frame is not None:
            try:
                mvsdk.CameraAlignFree(self._p_frame)
            except Exception:
                pass
            self._p_frame = None

    def set_exposure_ms(self, exposure_ms: float) -> None:
        self.exposure_ms = max(0.1, float(exposure_ms))
        if self._h_camera is not None:
            mvsdk.CameraSetExposureTime(self._h_camera, int(self.exposure_ms * 1000))

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def status(self) -> dict:
        with self._lock:
            return {
                "exposure_ms": self.exposure_ms,
                "shape": self._shape,
                "fps": round(self._fps, 1),
                "error": self._error,
                "mono": self._mono,
            }

    def _run(self) -> None:
        last = time.time()
        frames = 0
        while not self._stop.is_set():
            try:
                p_raw, head = mvsdk.CameraGetImageBuffer(self._h_camera, 1000)
                mvsdk.CameraImageProcess(self._h_camera, p_raw, self._p_frame, head)
                mvsdk.CameraReleaseImageBuffer(self._h_camera, p_raw)
                if platform.system() == "Windows":
                    mvsdk.CameraFlipFrameBuffer(self._p_frame, head, 1)
                buf = (mvsdk.c_ubyte * head.uBytes).from_address(self._p_frame)
                frame = np.frombuffer(buf, dtype=np.uint8)
                channels = 1 if head.uiMediaType == mvsdk.CAMERA_MEDIA_TYPE_MONO8 else 3
                frame = frame.reshape((head.iHeight, head.iWidth, channels))
                # Overlay focus aids: center crosshair + mean brightness
                vis = frame if channels == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                h, w = vis.shape[:2]
                cx, cy = w // 2, h // 2
                cv2.line(vis, (cx - 40, cy), (cx + 40, cy), (0, 255, 0), 1)
                cv2.line(vis, (cx, cy - 40), (cx, cy + 40), (0, 255, 0), 1)
                cv2.rectangle(vis, (cx - 80, cy - 80), (cx + 80, cy + 80), (0, 255, 0), 1)
                mean = float(frame.mean())
                label = f"exp={self.exposure_ms:.0f}ms  mean={mean:.0f}  {w}x{h}"
                cv2.putText(vis, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                ok, enc = cv2.imencode(
                    ".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
                )
                if ok:
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
            except mvsdk.CameraException as exc:
                if exc.error_code != mvsdk.CAMERA_STATUS_TIME_OUT:
                    with self._lock:
                        self._error = f"{exc.error_code}: {exc.message}"
                time.sleep(0.05)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>TiltyTable camera focus</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
         background: #111; color: #eee; }}
  header {{ padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 12px;
           align-items: center; background: #1a1a1a; border-bottom: 1px solid #333; }}
  h1 {{ font-size: 1rem; margin: 0; font-weight: 600; }}
  .meta {{ opacity: 0.75; font-size: 0.85rem; }}
  form {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  input[type=number] {{ width: 6rem; padding: 6px 8px; border-radius: 6px;
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
  <h1>MindVision live focus</h1>
  <span class="meta" id="status">…</span>
  <form action="/set" method="get">
    <label>Exposure (ms)
      <input type="number" name="exposure_ms" id="exp" min="0.1" max="2000" step="1" value="{exposure}"/>
    </label>
    <button type="submit">Apply</button>
  </form>
  <a class="chip" href="/set?exposure_ms=50">50</a>
  <a class="chip" href="/set?exposure_ms=100">100</a>
  <a class="chip" href="/set?exposure_ms=200">200</a>
  <a class="chip" href="/set?exposure_ms=500">500</a>
</header>
<main>
  <img src="/stream" alt="live camera stream"/>
  <p class="hint">
    Use the green crosshair / box as a focus target on the table surface.
    Raise exposure if the image is too dark. Adjust the lens focus ring while watching this page.
  </p>
</main>
<script>
async function refresh() {{
  try {{
    const r = await fetch('/status');
    const j = await r.json();
    document.getElementById('status').textContent =
      `${{j.shape[1]}}×${{j.shape[0]}}  ${{j.fps}} fps  exp=${{j.exposure_ms}}ms` +
      (j.error ? `  ERR ${{j.error}}` : '');
    document.getElementById('exp').value = j.exposure_ms;
  }} catch (e) {{}}
}}
setInterval(refresh, 1000); refresh();
</script>
</body>
</html>
"""


def make_handler(stream: MindVisionStream):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                body = PAGE.format(exposure=stream.exposure_ms).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/status":
                import json

                body = json.dumps(stream.status()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/set":
                if "exposure_ms" in qs:
                    stream.set_exposure_ms(float(qs["exposure_ms"][0]))
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if parsed.path == "/stream":
                if "exposure_ms" in qs:
                    stream.set_exposure_ms(float(qs["exposure_ms"][0]))
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
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--exposure-ms", type=float, default=200.0)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    args = parser.parse_args()

    stream = MindVisionStream(exposure_ms=args.exposure_ms, jpeg_quality=args.jpeg_quality)
    stream.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(stream))
    print(f"Focus preview: http://{args.host}:{args.port}/  (use Jetson LAN IP)")
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
