#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="--hardware"
KIOSK=1
HTTP_PORT="${TILTYTABLE_ARCADE_PORT:-8080}"

for arg in "$@"; do
  case "$arg" in
    --simulation) MODE="" ;;
    --no-kiosk) KIOSK=0 ;;
    --help)
      echo "Usage: ./run_arcade.sh [--simulation] [--no-kiosk]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ ! -x ".venv/bin/python3" ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

SERVER_ARGS=(--host 0.0.0.0 --port "$HTTP_PORT")
PREFLIGHT_ARGS=(--port "$HTTP_PORT")
if [[ -n "$MODE" ]]; then
  SERVER_ARGS+=("$MODE")
  PREFLIGHT_ARGS+=(--hardware)
fi
if [[ "$KIOSK" -eq 1 ]]; then
  PREFLIGHT_ARGS+=(--check-browser)
fi
if [[ -n "${TILTYTABLE_KINECT_URL:-}" ]]; then
  SERVER_ARGS+=(--kinect-url "$TILTYTABLE_KINECT_URL")
fi

".venv/bin/python3" -m arcade.preflight "${PREFLIGHT_ARGS[@]}"

".venv/bin/python3" -m arcade.server "${SERVER_ARGS[@]}" &
SERVER_PID=$!
BROWSER_PID=""

cleanup() {
  if [[ -n "$BROWSER_PID" ]]; then
    kill "$BROWSER_PID" 2>/dev/null || true
  fi
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

URL="http://127.0.0.1:${HTTP_PORT}"
for _ in $(seq 1 50); do
  if ".venv/bin/python3" -c "import urllib.request; urllib.request.urlopen('${URL}/api/state', timeout=.2)" \
      >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

echo "TiltyTable Arcade: $URL"

if [[ "$KIOSK" -eq 1 ]]; then
  # Cursor/SSH shells do not inherit the local GNOME session even though the
  # projector desktop is already running. Attach kiosk Chromium to that X
  # session and its audio/DBus runtime automatically.
  if [[ -z "${DISPLAY:-}" && -S /tmp/.X11-unix/X0 ]]; then
    export DISPLAY=:0
    local_runtime="/run/user/$(id -u)"
    if [[ -r "$local_runtime/gdm/Xauthority" ]]; then
      export XAUTHORITY="$local_runtime/gdm/Xauthority"
    fi
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$local_runtime}"
    if [[ -S "$local_runtime/bus" ]]; then
      export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$local_runtime/bus}"
    fi
    echo "Using local projector display $DISPLAY"
  fi

  if command -v chromium-browser >/dev/null 2>&1; then
    SNAP_REEXEC=0 chromium-browser --kiosk --app="$URL" --window-size=854,480 \
      --no-first-run --disable-gpu &
    BROWSER_PID=$!
  elif command -v chromium >/dev/null 2>&1; then
    SNAP_REEXEC=0 chromium --kiosk --app="$URL" --window-size=854,480 \
      --no-first-run --disable-gpu &
    BROWSER_PID=$!
  elif command -v firefox >/dev/null 2>&1; then
    firefox --kiosk "$URL" &
    BROWSER_PID=$!
  else
    echo "No Chromium/Firefox installation found. Open $URL from another browser," >&2
    echo "or install a kiosk-capable browser on the Jetson." >&2
  fi
fi

wait "$SERVER_PID"

