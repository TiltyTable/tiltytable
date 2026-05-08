#!/usr/bin/env python3
"""
Web-based HSV calibration for the blue ball tracker.

Usage
-----
    python3 ball_calibrate.py                        # serves on :8081
    python3 ball_calibrate.py --http-port 9000

Then open  http://<device-ip>:8081/  in your browser.

Workflow
--------
1. Live colour feed + mask preview stream to the browser.
2. Click and drag on the colour pane to auto-compute HSV bounds.
3. Fine-tune with the sliders (changes apply immediately to the stream).
4. Click Save to write  ball_hsv_calibration.json  next to this script.

The saved file is consumed by BallTracker.from_calibration_file().
"""

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
from pyk4a import (
    Config,
    DepthMode,
    ImageFormat,
    K4AException,
    K4ATimeoutException,
    PyK4A,
    connected_device_count,
)

from live_capture_viewer import (
    COLOR_RESOLUTIONS,
    DEPTH_ENGINE_DISPLAY,
    FPS_VALUES,
    color_to_bgr,
    set_display,
)

ROOT_DIR    = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT_DIR / "ball_hsv_calibration.json"

_H_PAD = 10
_S_PAD = 40
_V_PAD = 60
_OVERLAY_BGR   = np.array([50, 220, 50], dtype=np.uint8)
_OVERLAY_ALPHA = 0.45

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class CalibrationState:
    def __init__(self, output_path):
        self.output_path = Path(output_path)
        self.lock = threading.Lock()
        self.hsv_low  = np.array([100,  60,  30], dtype=np.uint8)
        self.hsv_high = np.array([130, 255, 255], dtype=np.uint8)
        self._last_color_bgr = None
        self._load()

    def _load(self):
        if not self.output_path.exists():
            return
        try:
            data = json.loads(self.output_path.read_text(encoding="utf-8"))
            self.hsv_low  = np.array(data["hsv_low"],  dtype=np.uint8)
            self.hsv_high = np.array(data["hsv_high"], dtype=np.uint8)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass

    def get_bounds(self):
        with self.lock:
            return self.hsv_low.copy(), self.hsv_high.copy()

    def set_bounds(self, h_lo, h_hi, s_lo, s_hi, v_lo, v_hi):
        with self.lock:
            self.hsv_low  = np.array([h_lo, s_lo, v_lo], dtype=np.uint8)
            self.hsv_high = np.array([h_hi, s_hi, v_hi], dtype=np.uint8)

    def store_frame(self, color_bgr):
        with self.lock:
            self._last_color_bgr = color_bgr

    def auto_select(self, sx, sy, sw, sh, display_width):
        """
        Compute HSV bounds from a rect (sx,sy,sw,sh) given in stream-image
        coords (i.e. left-pane pixel space at display_width resolution).
        Returns True on success.
        """
        with self.lock:
            frame = self._last_color_bgr
        if frame is None or sw <= 0 or sh <= 0:
            return False

        h_src, w_src = frame.shape[:2]
        scale = w_src / display_width

        x0 = int(np.clip(sx * scale,        0, w_src - 1))
        y0 = int(np.clip(sy * scale,        0, h_src - 1))
        x1 = int(np.clip((sx + sw) * scale, 0, w_src - 1))
        y1 = int(np.clip((sy + sh) * scale, 0, h_src - 1))
        if x1 <= x0 or y1 <= y0:
            return False

        roi     = frame[y0:y1, x0:x1]
        samples = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3)
        h_mn, s_mn, v_mn = samples.min(axis=0)
        h_mx, s_mx, v_mx = samples.max(axis=0)

        with self.lock:
            self.hsv_low  = np.array([
                np.clip(int(h_mn) - _H_PAD, 0, 179),
                np.clip(int(s_mn) - _S_PAD, 0, 255),
                np.clip(int(v_mn) - _V_PAD, 0, 255),
            ], dtype=np.uint8)
            self.hsv_high = np.array([
                np.clip(int(h_mx) + _H_PAD, 0, 179),
                np.clip(int(s_mx) + _S_PAD, 0, 255),
                np.clip(int(v_mx) + _V_PAD, 0, 255),
            ], dtype=np.uint8)
        return True

    def save(self):
        low, high = self.get_bounds()
        data = {"hsv_low": low.tolist(), "hsv_high": high.tolist()}
        tmp = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.output_path)
        return data

    def to_json(self):
        low, high = self.get_bounds()
        return {
            "hsv_low":  low.tolist(),
            "hsv_high": high.tolist(),
            "output_path": str(self.output_path),
        }


# ---------------------------------------------------------------------------
# Frame capture + rendering thread
# ---------------------------------------------------------------------------

class CalibrationHub:
    def __init__(self, state, args):
        self.state = state
        self.args  = args
        self.cond  = threading.Condition()
        self.stop_event = threading.Event()
        self.thread = None
        self.k4a    = None
        self.seq    = 0
        self.jpeg   = _make_placeholder(args.display_width)

    def start(self):
        self.thread = threading.Thread(target=self._run, name="calib-capture", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.cond:
            self.cond.notify_all()
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.k4a and self.k4a.is_running:
            self.k4a.stop()

    def wait_for_jpeg(self, last_seq, timeout=1.0):
        with self.cond:
            if self.seq == last_seq:
                self.cond.wait(timeout=timeout)
            return self.seq, self.jpeg

    def _run(self):
        args = self.args
        if args.depth_engine_display:
            set_display(args.depth_engine_display, "depth engine", quiet=True)
        try:
            n = connected_device_count()
            if n <= args.device_id:
                print(f"No Kinect at index {args.device_id} ({n} found).", file=sys.stderr)
                return
            config = Config(
                color_resolution=COLOR_RESOLUTIONS[args.color_resolution],
                color_format=ImageFormat.COLOR_BGRA32,
                depth_mode=DepthMode.OFF,
                camera_fps=FPS_VALUES[args.fps],
                synchronized_images_only=False,
            )
            self.k4a = PyK4A(config=config, device_id=args.device_id)
            self.k4a.start()

            while not self.stop_event.is_set():
                try:
                    capture = self.k4a.get_capture(timeout=1000)
                except K4ATimeoutException:
                    continue

                color_bgr = color_to_bgr(capture.color)
                if color_bgr is None:
                    continue

                self.state.store_frame(color_bgr)
                jpeg = self._render(color_bgr)

                with self.cond:
                    self.seq  += 1
                    self.jpeg  = jpeg
                    self.cond.notify_all()

        except (K4AException, RuntimeError, cv2.error) as exc:
            print(f"Capture error: {exc}", file=sys.stderr)
        finally:
            if self.k4a and self.k4a.is_running:
                self.k4a.stop()

    def _render(self, color_bgr):
        w = self.args.display_width
        h_src, w_src = color_bgr.shape[:2]
        dh = max(1, int(round(h_src * w / w_src)))

        small = cv2.resize(color_bgr, (w, dh), interpolation=cv2.INTER_AREA)
        hsv   = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        low, high = self.state.get_bounds()
        mask = cv2.inRange(hsv, low, high)

        overlay        = small.copy()
        overlay[mask > 0] = _OVERLAY_BGR
        left = cv2.addWeighted(small, 1 - _OVERLAY_ALPHA, overlay, _OVERLAY_ALPHA, 0)

        right = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

        label = (f"H {low[0]}-{high[0]}  "
                 f"S {low[1]}-{high[1]}  "
                 f"V {low[2]}-{high[2]}")
        for pane in (left, right):
            cv2.rectangle(pane, (0, dh - 22), (w, dh), (0, 0, 0), -1)
            cv2.putText(pane, label, (6, dh - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1, cv2.LINE_AA)

        combined = np.hstack((left, right))
        ok, buf  = cv2.imencode(".jpg", combined, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        return buf.tobytes()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class CalibrationHandler(BaseHTTPRequestHandler):
    hub   = None
    state = None
    args  = None

    def log_message(self, fmt, *args):
        pass  # silence per-request logs

    def do_GET(self):
        if self.path == "/":
            self._send_html(_HTML)
        elif self.path == "/stream/calibration.mjpg":
            self._serve_mjpeg()
        elif self.path == "/api/state":
            self._send_json(self.state.to_json())
        else:
            self.send_error(404)

    def do_POST(self):
        try:
            data = self._read_json()
            if self.path == "/api/bounds":
                self.state.set_bounds(
                    int(data["h_lo"]), int(data["h_hi"]),
                    int(data["s_lo"]), int(data["s_hi"]),
                    int(data["v_lo"]), int(data["v_hi"]),
                )
                self._send_json(self.state.to_json())
            elif self.path == "/api/select":
                ok = self.state.auto_select(
                    int(data["x"]), int(data["y"]),
                    int(data["w"]), int(data["h"]),
                    self.args.display_width,
                )
                self._send_json({**self.state.to_json(), "ok": ok})
            elif self.path == "/api/save":
                saved = self.state.save()
                print(f"Saved calibration: {saved}")
                self._send_json({"ok": True, "saved": saved})
            else:
                self.send_error(404)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        last_seq = -1
        while True:
            seq, jpeg = self.hub.wait_for_jpeg(last_seq)
            last_seq = seq
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class CalibrationHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
# Placeholder frame (shown before camera is ready)
# ---------------------------------------------------------------------------

def _make_placeholder(display_width):
    w, h = display_width * 2, 360
    img = np.full((h, w, 3), 30, dtype=np.uint8)
    cv2.putText(img, "Waiting for camera...", (32, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (180, 180, 180), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else b""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Web HSV calibration for the ball tracker.")
    parser.add_argument("--host",      default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8081)
    parser.add_argument("--output",    default=str(DEFAULT_OUT))
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--color-resolution", choices=sorted(COLOR_RESOLUTIONS), default="720p")
    parser.add_argument("--fps",       choices=sorted(FPS_VALUES, key=int), default="30")
    parser.add_argument("--depth-engine-display", default=DEPTH_ENGINE_DISPLAY)
    parser.add_argument("--display-width", type=int, default=640,
                        help="Width of each pane in the streamed view")
    return parser.parse_args()


def main():
    args  = parse_args()
    state = CalibrationState(args.output)
    hub   = CalibrationHub(state, args)

    CalibrationHandler.hub   = hub
    CalibrationHandler.state = state
    CalibrationHandler.args  = args

    hub.start()
    server = CalibrationHTTPServer((args.host, args.http_port), CalibrationHandler)
    print(f"Calibration UI at  http://{args.host}:{args.http_port}/")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        hub.stop()
        server.server_close()
    return 0


# ---------------------------------------------------------------------------
# Inline HTML/JS UI
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ball HSV Calibration</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #12121f; color: #ddd; font-family: monospace;
         display: flex; height: 100vh; overflow: hidden; }
  #stream-pane { flex: 1; display: flex; flex-direction: column;
                 align-items: flex-start; padding: 12px; overflow: hidden; }
  #stream-pane h2 { font-size: 12px; text-transform: uppercase;
                    color: #888; margin-bottom: 8px; }
  #stream-wrap { position: relative; display: inline-block; max-width: 100%; }
  #stream-img  { display: block; max-width: 100%; }
  #sel-canvas  { position: absolute; inset: 0; cursor: crosshair; }
  #controls    { width: 260px; background: #1a1a30; padding: 16px;
                 display: flex; flex-direction: column; gap: 10px;
                 overflow-y: auto; }
  h1 { font-size: 13px; text-transform: uppercase; color: #aaa; }
  .row { display: flex; flex-direction: column; gap: 3px; }
  .row label { display: flex; justify-content: space-between;
               font-size: 12px; color: #bbb; }
  .row input[type=range] { width: 100%; accent-color: #5a9af5; }
  hr { border: none; border-top: 1px solid #2a2a44; }
  button { padding: 9px; background: #1e3a6e; color: #ddd;
           border: 1px solid #3a5aae; cursor: pointer; font-size: 13px;
           font-family: monospace; }
  button:hover { background: #274f96; }
  #status { font-size: 11px; color: #7c7; min-height: 2em;
            word-break: break-all; white-space: pre-wrap; }
  .hint { font-size: 11px; color: #666; }
</style>
</head>
<body>
<div id="stream-pane">
  <h2>Colour feed (left) &nbsp;|&nbsp; Mask (right)</h2>
  <div id="stream-wrap">
    <img id="stream-img" src="/stream/calibration.mjpg" alt="stream">
    <canvas id="sel-canvas"></canvas>
  </div>
</div>
<div id="controls">
  <h1>HSV Calibration</h1>
  <p class="hint">Drag on the colour pane to sample the ball.</p>
  <hr>

  <div class="row">
    <label>H low <span id="lbl-hlo">100</span></label>
    <input type="range" id="s-hlo" min="0" max="179" value="100">
  </div>
  <div class="row">
    <label>H high <span id="lbl-hhi">130</span></label>
    <input type="range" id="s-hhi" min="0" max="179" value="130">
  </div>
  <hr>
  <div class="row">
    <label>S low <span id="lbl-slo">60</span></label>
    <input type="range" id="s-slo" min="0" max="255" value="60">
  </div>
  <div class="row">
    <label>S high <span id="lbl-shi">255</span></label>
    <input type="range" id="s-shi" min="0" max="255" value="255">
  </div>
  <hr>
  <div class="row">
    <label>V low <span id="lbl-vlo">30</span></label>
    <input type="range" id="s-vlo" min="0" max="255" value="30">
  </div>
  <div class="row">
    <label>V high <span id="lbl-vhi">255</span></label>
    <input type="range" id="s-vhi" min="0" max="255" value="255">
  </div>
  <hr>

  <button id="btn-save">Save calibration</button>
  <div id="status"></div>
</div>

<script>
const sliders = {
  hlo: document.getElementById('s-hlo'),
  hhi: document.getElementById('s-hhi'),
  slo: document.getElementById('s-slo'),
  shi: document.getElementById('s-shi'),
  vlo: document.getElementById('s-vlo'),
  vhi: document.getElementById('s-vhi'),
};
const labels = {
  hlo: document.getElementById('lbl-hlo'),
  hhi: document.getElementById('lbl-hhi'),
  slo: document.getElementById('lbl-slo'),
  shi: document.getElementById('lbl-shi'),
  vlo: document.getElementById('lbl-vlo'),
  vhi: document.getElementById('lbl-vhi'),
};
const status  = document.getElementById('status');
const canvas  = document.getElementById('sel-canvas');
const img     = document.getElementById('stream-img');
const ctx     = canvas.getContext('2d');

// ── keep canvas pixel size in sync with the displayed image size ──────────
function syncCanvas() {
  canvas.width  = img.clientWidth;
  canvas.height = img.clientHeight;
}
new ResizeObserver(syncCanvas).observe(img);
img.addEventListener('load', syncCanvas);

// ── slider → server ───────────────────────────────────────────────────────
let debounce = null;
function onSliderInput(key) {
  labels[key].textContent = sliders[key].value;
  clearTimeout(debounce);
  debounce = setTimeout(pushBounds, 60);
}
Object.entries(sliders).forEach(([k, el]) =>
  el.addEventListener('input', () => onSliderInput(k)));

function pushBounds() {
  fetch('/api/bounds', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      h_lo: +sliders.hlo.value, h_hi: +sliders.hhi.value,
      s_lo: +sliders.slo.value, s_hi: +sliders.shi.value,
      v_lo: +sliders.vlo.value, v_hi: +sliders.vhi.value,
    }),
  }).then(r => r.json()).then(applyState);
}

function applyState(s) {
  const lo = s.hsv_low, hi = s.hsv_high;
  sliders.hlo.value = lo[0]; labels.hlo.textContent = lo[0];
  sliders.hhi.value = hi[0]; labels.hhi.textContent = hi[0];
  sliders.slo.value = lo[1]; labels.slo.textContent = lo[1];
  sliders.shi.value = hi[1]; labels.shi.textContent = hi[1];
  sliders.vlo.value = lo[2]; labels.vlo.textContent = lo[2];
  sliders.vhi.value = hi[2]; labels.vhi.textContent = hi[2];
}

// ── drag selection ────────────────────────────────────────────────────────
let drag = null;

canvas.addEventListener('mousedown', e => {
  const r = canvas.getBoundingClientRect();
  drag = { x0: e.clientX - r.left, y0: e.clientY - r.top };
});

canvas.addEventListener('mousemove', e => {
  if (!drag) return;
  const r  = canvas.getBoundingClientRect();
  drag.x1  = e.clientX - r.left;
  drag.y1  = e.clientY - r.top;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(drag.x0, drag.y0, drag.x1 - drag.x0, drag.y1 - drag.y0);
});

canvas.addEventListener('mouseup', e => {
  if (!drag) return;
  const r  = canvas.getBoundingClientRect();
  drag.x1  = e.clientX - r.left;
  drag.y1  = e.clientY - r.top;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Map CSS coords → stream-image left-pane coords.
  // The stream image is display_width*2 wide; left pane is the first half.
  const scaleX = img.naturalWidth / img.clientWidth;
  const scaleY = img.naturalHeight / img.clientHeight;
  const x = Math.round(Math.min(drag.x0, drag.x1) * scaleX);
  const y = Math.round(Math.min(drag.y0, drag.y1) * scaleY);
  const w = Math.round(Math.abs(drag.x1 - drag.x0) * scaleX);
  const h = Math.round(Math.abs(drag.y1 - drag.y0) * scaleY);
  drag = null;

  if (w < 4 || h < 4) return;
  fetch('/api/select', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ x, y, w, h }),
  }).then(r => r.json()).then(s => {
    applyState(s);
    status.textContent = s.ok ? 'Bounds updated from selection.' : 'Selection too small.';
  });
});

// ── save ──────────────────────────────────────────────────────────────────
document.getElementById('btn-save').addEventListener('click', () => {
  fetch('/api/save', { method: 'POST' })
    .then(r => r.json())
    .then(s => {
      status.textContent = s.ok
        ? 'Saved ✓  ' + JSON.stringify(s.saved)
        : 'Save failed: ' + s.error;
    });
});

// ── initial state sync ────────────────────────────────────────────────────
fetch('/api/state').then(r => r.json()).then(applyState);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
