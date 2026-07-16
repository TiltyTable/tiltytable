#!/usr/bin/env python3
"""
Ball balancer for the UIM5756PM Stewart platform.

Reads ball position from BallTracker (Kinect, overhead), runs a PD/PID
controller per axis, and sends pose commands to the Arduino via serial.

Coordinate mapping (camera mounted overhead, looking down at table):
  camera x_mm  →  table lateral (left/right)  →  controls roll
  camera y_mm  →  table fore-aft              →  controls pitch

Both axes are independent PID loops.  Start with Kp only (set Ki, Kd = 0),
verify the tilt direction corrects the error (flip --roll-sign / --pitch-sign
if backwards), then add Kd (~5–10× Kp) to dampen oscillation.

Usage:
    python3 ball_balancer.py --port /dev/ttyUSB0 [options]
"""

import argparse
import time

import numpy as np
import serial

from ball_tracker import BallTracker


# ─── PID ──────────────────────────────────────────────────────────────────────

class PIDController:
    """Single-axis PID with anti-windup clamping."""

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        output_limit: float,
        integral_limit: float,
    ):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_limit = output_limit
        self.integral_limit = integral_limit
        self._integral = 0.0
        self._last_error = 0.0
        self._initialized = False

    def reset(self) -> None:
        self._integral = 0.0
        self._last_error = 0.0
        self._initialized = False

    def update(self, error: float, dt: float) -> float:
        if not self._initialized:
            self._last_error = error
            self._initialized = True

        self._integral += error * dt
        self._integral = float(np.clip(self._integral, -self.integral_limit, self.integral_limit))

        derivative = (error - self._last_error) / max(dt, 1e-6)
        self._last_error = error

        output = self.kp * error + self.ki * self._integral + self.kd * derivative
        return float(np.clip(output, -self.output_limit, self.output_limit))


# ─── Balancer ─────────────────────────────────────────────────────────────────

class BallBalancer:
    """
    Closed-loop ball balancer.

    Parameters
    ----------
    port            Serial port connected to the Arduino.
    tracker         Configured BallTracker instance.
    pid_roll        PID for the roll axis (driven by camera x_mm error).
    pid_pitch       PID for the pitch axis (driven by camera y_mm error).
    setpoint_mm     Target (x_mm, y_mm) in camera space; (0, 0) means optical
                    axis.  Use --setpoint-x/y to shift to physical table center.
    max_roll_deg    Safety clamp on commanded roll (default 4°, hard limit 5°).
    max_pitch_deg   Safety clamp on commanded pitch.
    roll_sign       +1 or -1; flip if table tilts the wrong way for X error.
    pitch_sign      +1 or -1; flip if table tilts the wrong way for Y error.
    lost_timeout_s  Seconds without a detection before levelling the table.
    """

    def __init__(
        self,
        port: str,
        tracker: BallTracker,
        pid_roll: PIDController,
        pid_pitch: PIDController,
        setpoint_mm: tuple[float, float] = (0.0, 0.0),
        max_roll_deg: float = 4.0,
        max_pitch_deg: float = 4.0,
        roll_sign: float = 1.0,
        pitch_sign: float = 1.0,
        lost_timeout_s: float = 1.0,
    ):
        self.tracker = tracker
        self.pid_roll = pid_roll
        self.pid_pitch = pid_pitch
        self.setpoint_mm = setpoint_mm
        self.max_roll_deg = max_roll_deg
        self.max_pitch_deg = max_pitch_deg
        self.roll_sign = roll_sign
        self.pitch_sign = pitch_sign
        self.lost_timeout_s = lost_timeout_s

        self._ser = serial.Serial(port, 115200, timeout=1.0)
        # Arduino resets on DTR; wait for it to boot before sending commands.
        time.sleep(2.0)
        self._drain_serial()

    # ── Serial helpers ────────────────────────────────────────────────────────

    def _drain_serial(self) -> None:
        while self._ser.in_waiting:
            self._ser.readline()

    def _send(self, cmd: str) -> None:
        self._ser.write((cmd + "\n").encode())

    def _enable_table(self) -> None:
        self._send("enable")
        time.sleep(0.1)
        self._send("zero")   # home steppers to known position
        time.sleep(0.5)

    def _level_table(self) -> None:
        self._send("pose 0.0000 0.0000 0.0000")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, k4a) -> None:
        """
        Run the balance loop until Ctrl-C.

        k4a must be a started PyK4A instance configured with:
          - color_format = COLOR_BGRA32
          - synchronized_images_only = True
        """
        self._enable_table()
        print(
            f"Balancer running — setpoint ({self.setpoint_mm[0]:.1f}, {self.setpoint_mm[1]:.1f}) mm. "
            "Ctrl-C to stop.\n"
        )

        last_t = time.monotonic()
        last_seen_t = last_t
        levelled = False

        try:
            while True:
                try:
                    cap = k4a.get_capture(timeout=1000)
                except Exception:
                    continue

                # IR and depth are natively co-registered on the depth sensor.
                ir       = cap.ir
                depth_mm = cap.depth

                if ir is None or depth_mm is None:
                    continue

                now = time.monotonic()
                dt  = max(now - last_t, 1e-3)
                last_t = now

                pos, _det = self.tracker.update(ir, depth_mm)

                if pos is None:
                    # No ball detected.
                    elapsed_lost = now - last_seen_t
                    if elapsed_lost > self.lost_timeout_s and not levelled:
                        self._level_table()
                        self.pid_roll.reset()
                        self.pid_pitch.reset()
                        levelled = True
                    status = f"[no ball   lost={elapsed_lost:.1f}s]"
                    print(f"\r{status:<60}", end="", flush=True)
                    continue

                last_seen_t = now
                levelled = False

                bx, by = pos[0], pos[1]  # camera-frame X and Y in mm
                err_x = bx - self.setpoint_mm[0]
                err_y = by - self.setpoint_mm[1]

                roll  = self.roll_sign  * self.pid_roll.update(err_x, dt)
                pitch = self.pitch_sign * self.pid_pitch.update(err_y, dt)

                # Final safety clamp (Arduino also has its own limit).
                roll  = float(np.clip(roll,  -self.max_roll_deg,  self.max_roll_deg))
                pitch = float(np.clip(pitch, -self.max_pitch_deg, self.max_pitch_deg))

                self._send(f"pose {roll:.4f} {pitch:.4f} 0.0000")

                print(
                    f"\rball=({bx:+7.1f},{by:+7.1f})mm  "
                    f"err=({err_x:+7.1f},{err_y:+7.1f})mm  "
                    f"roll={roll:+6.3f}°  pitch={pitch:+6.3f}°  ",
                    end="",
                    flush=True,
                )

        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            self._level_table()
            time.sleep(0.5)
            self._send("disable")
            self._ser.close()
            print("Table levelled and disabled.")


# ─── Entry point ──────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="PID ball balancer for the UIM5756PM Stewart platform.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Hardware
    ap.add_argument("--port", default="/dev/ttyUSB0",
                    help="Arduino serial port")
    ap.add_argument("--device-id", type=int, default=0,
                    help="Kinect device index")
    ap.add_argument("--depth-mode", default="nfov_unbinned",
                    choices=["nfov_unbinned", "nfov_2x2binned", "wfov_unbinned",
                             "wfov_2x2binned", "passive_ir"])
    ap.add_argument("--fps", default="30", choices=["5", "15", "30"])
    ap.add_argument("--ball-radius-min", type=float, default=20.0, metavar="MM")
    ap.add_argument("--ball-radius-max", type=float, default=40.0, metavar="MM")
    ap.add_argument("--ir-thresh", type=float, default=0.5, metavar="FRAC",
                    help="Dark if below FRAC × local background (0–1)")
    # Setpoint — camera-space mm of the physical table centre.
    # Run with a stationary ball at the desired centre, read its position,
    # then pass those values here.
    ap.add_argument("--setpoint-x", type=float, default=0.0, metavar="MM",
                    help="Camera x_mm corresponding to the table centre")
    ap.add_argument("--setpoint-y", type=float, default=0.0, metavar="MM",
                    help="Camera y_mm corresponding to the table centre")

    # PID for the roll axis (driven by X error, ball left-right)
    ap.add_argument("--kp-roll", type=float, default=0.04,
                    help="Roll proportional gain (deg per mm error)")
    ap.add_argument("--ki-roll", type=float, default=0.0,
                    help="Roll integral gain")
    ap.add_argument("--kd-roll", type=float, default=0.2,
                    help="Roll derivative gain (damping)")

    # PID for the pitch axis (driven by Y error, ball fore-aft)
    ap.add_argument("--kp-pitch", type=float, default=0.04,
                    help="Pitch proportional gain (deg per mm error)")
    ap.add_argument("--ki-pitch", type=float, default=0.0,
                    help="Pitch integral gain")
    ap.add_argument("--kd-pitch", type=float, default=0.2,
                    help="Pitch derivative gain (damping)")

    # Sign convention — flip if tilt direction is backwards
    ap.add_argument("--roll-sign",  type=float, default=1.0,  choices=[1.0, -1.0],
                    help="+1 or -1; flip if +X error causes table to tilt the wrong way")
    ap.add_argument("--pitch-sign", type=float, default=1.0, choices=[1.0, -1.0],
                    help="+1 or -1; flip if +Y error causes table to tilt the wrong way")

    # Safety
    ap.add_argument("--max-roll",  type=float, default=4.0, metavar="DEG",
                    help="Maximum commanded roll angle")
    ap.add_argument("--max-pitch", type=float, default=4.0, metavar="DEG",
                    help="Maximum commanded pitch angle")
    ap.add_argument("--lost-timeout", type=float, default=1.0, metavar="S",
                    help="Seconds without detection before levelling table")

    return ap


def main() -> None:
    args = _build_arg_parser().parse_args()

    # ── Camera ────────────────────────────────────────────────────────────────
    from pyk4a import (
        CameraFPS,
        Config,
        DepthMode,
        PyK4A,
        connected_device_count,
    )

    _DEPTH_MODE = {
        "nfov_unbinned":    DepthMode.NFOV_UNBINNED,
        "nfov_2x2binned":   DepthMode.NFOV_2X2BINNED,
        "wfov_unbinned":    DepthMode.WFOV_UNBINNED,
        "wfov_2x2binned":   DepthMode.WFOV_2X2BINNED,
        "passive_ir":       DepthMode.PASSIVE_IR,
    }
    _FPS = {"5": CameraFPS.FPS_5, "15": CameraFPS.FPS_15, "30": CameraFPS.FPS_30}

    n_devices = connected_device_count()
    if n_devices <= args.device_id:
        raise SystemExit(f"No Kinect at index {args.device_id} ({n_devices} found).")

    config = Config(
        depth_mode=_DEPTH_MODE[args.depth_mode],
        camera_fps=_FPS[args.fps],
    )
    k4a = PyK4A(config=config, device_id=args.device_id)
    k4a.start()

    # ── Tracker ───────────────────────────────────────────────────────────────
    tracker = BallTracker.from_k4a_calibration(
        k4a.calibration,
        ball_radius_min_mm=args.ball_radius_min,
        ball_radius_max_mm=args.ball_radius_max,
        ir_thresh_fraction=args.ir_thresh,
    )

    # ── Controllers ───────────────────────────────────────────────────────────
    pid_roll = PIDController(
        kp=args.kp_roll,  ki=args.ki_roll,  kd=args.kd_roll,
        output_limit=args.max_roll,  integral_limit=args.max_roll * 20,
    )
    pid_pitch = PIDController(
        kp=args.kp_pitch, ki=args.ki_pitch, kd=args.kd_pitch,
        output_limit=args.max_pitch, integral_limit=args.max_pitch * 20,
    )

    balancer = BallBalancer(
        port=args.port,
        tracker=tracker,
        pid_roll=pid_roll,
        pid_pitch=pid_pitch,
        setpoint_mm=(args.setpoint_x, args.setpoint_y),
        max_roll_deg=args.max_roll,
        max_pitch_deg=args.max_pitch,
        roll_sign=args.roll_sign,
        pitch_sign=args.pitch_sign,
        lost_timeout_s=args.lost_timeout,
    )

    try:
        balancer.run(k4a)
    finally:
        if k4a.is_running:
            k4a.stop()


if __name__ == "__main__":
    main()
