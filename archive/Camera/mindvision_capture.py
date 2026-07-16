#!/usr/bin/env python3
"""MindVision HT-SUA134GM capture helper for the Jetson.

Uses the official MindVision Python binding (`camera/mvsdk.py`) from linuxSDK.
Requires ARM64 `libMVSDK.so` installed system-wide (see camera/README.md).
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

# Allow `python3 camera/mindvision_capture.py` from repo root.
_CAMERA_DIR = Path(__file__).resolve().parent
if str(_CAMERA_DIR) not in sys.path:
    sys.path.insert(0, str(_CAMERA_DIR))

import mvsdk  # noqa: E402


VENDOR_ID = "f622"
PRODUCT_HINT = "SUA134GM"


def usb_present() -> bool:
    try:
        out = subprocess.check_output(["lsusb"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return False
    low = out.lower()
    return VENDOR_ID in low or PRODUCT_HINT.lower() in low


def probe() -> int:
    usb = usb_present()
    print(f"USB MindVision present: {usb}")
    try:
        devices = mvsdk.CameraEnumerateDevice()
    except mvsdk.CameraException as exc:
        print(f"CameraEnumerateDevice failed: {exc.error_code} {exc.message}")
        return 2
    print(f"SDK enumerated cameras: {len(devices)}")
    for i, info in enumerate(devices):
        print(f"  [{i}] {info.GetFriendlyName()}  port={info.GetPortType()}")
    if not usb:
        print("Camera not on USB. Check cable / USB3 port.")
        return 1
    if not devices:
        print("USB present but SDK found 0 cameras — check udev rules / reboot / permissions.")
        return 3
    print("OK — try: python3 camera/mindvision_capture.py --save /tmp/sua134.png")
    return 0


def grab_one_png(out_path: Path, exposure_ms: float = 30.0) -> int:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        print(f"Need numpy+opencv in the venv: {exc}", file=sys.stderr)
        return 4

    devices = mvsdk.CameraEnumerateDevice()
    if not devices:
        print("No camera found via SDK.", file=sys.stderr)
        return 1

    info = devices[0]
    print(f"Opening: {info.GetFriendlyName()} ({info.GetPortType()})")
    try:
        h_camera = mvsdk.CameraInit(info, -1, -1)
    except mvsdk.CameraException as exc:
        print(f"CameraInit failed ({exc.error_code}): {exc.message}", file=sys.stderr)
        return 2

    try:
        cap = mvsdk.CameraGetCapability(h_camera)
        mono = cap.sIspCapacity.bMonoSensor != 0
        if mono:
            mvsdk.CameraSetIspOutFormat(h_camera, mvsdk.CAMERA_MEDIA_TYPE_MONO8)
        else:
            mvsdk.CameraSetIspOutFormat(h_camera, mvsdk.CAMERA_MEDIA_TYPE_BGR8)

        mvsdk.CameraSetTriggerMode(h_camera, 0)
        mvsdk.CameraSetAeState(h_camera, 0)
        mvsdk.CameraSetExposureTime(h_camera, int(exposure_ms * 1000))
        mvsdk.CameraPlay(h_camera)

        frame_bytes = (
            cap.sResolutionRange.iWidthMax
            * cap.sResolutionRange.iHeightMax
            * (1 if mono else 3)
        )
        p_frame = mvsdk.CameraAlignMalloc(frame_bytes, 16)

        try:
            p_raw, head = mvsdk.CameraGetImageBuffer(h_camera, 2000)
            mvsdk.CameraImageProcess(h_camera, p_raw, p_frame, head)
            mvsdk.CameraReleaseImageBuffer(h_camera, p_raw)

            if platform.system() == "Windows":
                mvsdk.CameraFlipFrameBuffer(p_frame, head, 1)

            buf = (mvsdk.c_ubyte * head.uBytes).from_address(p_frame)
            frame = np.frombuffer(buf, dtype=np.uint8)
            channels = 1 if head.uiMediaType == mvsdk.CAMERA_MEDIA_TYPE_MONO8 else 3
            frame = frame.reshape((head.iHeight, head.iWidth, channels))

            out_path = out_path.expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            ok = cv2.imwrite(str(out_path), frame)
            if not ok:
                print(f"cv2.imwrite failed for {out_path}", file=sys.stderr)
                return 5
            print(f"Saved {out_path}  shape={frame.shape}  exposure_ms={exposure_ms}")
            return 0
        except mvsdk.CameraException as exc:
            print(f"Grab failed ({exc.error_code}): {exc.message}", file=sys.stderr)
            return 3
        finally:
            mvsdk.CameraAlignFree(p_frame)
    finally:
        mvsdk.CameraUnInit(h_camera)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="list USB + SDK cameras")
    parser.add_argument("--save", type=Path, help="grab one frame to this PNG path")
    parser.add_argument("--exposure-ms", type=float, default=30.0, help="manual exposure (ms)")
    args = parser.parse_args()

    if args.probe or not args.save:
        code = probe()
        if not args.save:
            return code
        if code != 0:
            return code
    return grab_one_png(args.save, exposure_ms=args.exposure_ms)


if __name__ == "__main__":
    sys.exit(main())
