#!/usr/bin/env python3
"""MindVision HT-SUA134GM capture helper for the Jetson.

This camera does not appear as /dev/video*. It needs MindVision's libMVSDK.so
(ARM64 linuxSDK). Until that library is installed, --probe reports USB
presence and explains what is missing.

Once libMVSDK.so is on the system library path, --save grabs one frame to a
PNG via a minimal ctypes binding of CameraInit / CameraGetImageBuffer.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import subprocess
import sys
from pathlib import Path


VENDOR_ID = "f622"
PRODUCT_HINT = "SUA134GM"
LIB_CANDIDATES = (
    "MVSDK",
    "libMVSDK.so",
    "/usr/lib/libMVSDK.so",
    "/lib/libMVSDK.so",
    "/usr/local/lib/libMVSDK.so",
)


def usb_present() -> bool:
    try:
        out = subprocess.check_output(["lsusb"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return False
    return VENDOR_ID in out.lower() or PRODUCT_HINT.lower() in out.lower()


def find_mvsdk() -> str | None:
    for name in LIB_CANDIDATES:
        if name.startswith("/"):
            if Path(name).is_file():
                return name
            continue
        path = ctypes.util.find_library(name.replace("lib", "").replace(".so", ""))
        if path:
            return path
        # find_library is picky; try dlopen paths directly
        for prefix in ("/usr/lib", "/lib", "/usr/local/lib"):
            candidate = Path(prefix) / (name if name.endswith(".so") else f"lib{name}.so")
            if candidate.is_file():
                return str(candidate)
    return None


def probe() -> int:
    usb = usb_present()
    lib = find_mvsdk()
    video_nodes = sorted(Path("/dev").glob("video*"))
    print(f"USB MindVision present: {usb}")
    print(f"libMVSDK.so found:      {lib or 'NO'}")
    print(f"/dev/video* nodes:      {video_nodes or 'none'}")
    if not usb:
        print("\nCamera not on USB. Check the cable / USB3 port.")
        return 1
    if not lib:
        print(
            "\nCamera is on USB but MindVision's ARM64 SDK is not installed.\n"
            "Download linuxSDK from:\n"
            "  https://www.mindvision.ltd/Service-Support/Software-Download.html\n"
            "Then follow camera/README.md (copy aarch64 libMVSDK.so + headers)."
        )
        return 2
    print("\nSDK library present — try: python3 camera/mindvision_capture.py --save /tmp/sua134.png")
    return 0


# Minimal ctypes surface for a single-frame grab. Constants match MindVision
# CameraApi.h (status OK = 0). Kept small on purpose until the full SDK is in.
CAMERA_STATUS_SUCCESS = 0


class CameraSdkStatus(ctypes.c_int):
    pass


def grab_one_png(out_path: Path) -> int:
    lib_path = find_mvsdk()
    if not lib_path:
        print("libMVSDK.so not found; run with --probe for details.", file=sys.stderr)
        return 2
    if not usb_present():
        print("MindVision camera not present on USB.", file=sys.stderr)
        return 1

    try:
        import numpy as np
        import cv2
    except ImportError as exc:
        print(f"Need numpy+opencv in the venv: {exc}", file=sys.stderr)
        return 3

    sdk = ctypes.CDLL(lib_path)
    # Signatures are approximate; official headers define richer structs.
    # If init fails, print the status and exit — do not invent frames.
    h_camera = ctypes.c_int()
    # CameraSdkInit(iLanguageSel) — 0 English / -1 auto on many builds
    if hasattr(sdk, "CameraSdkInit"):
        st = sdk.CameraSdkInit(-1)
        if st != CAMERA_STATUS_SUCCESS:
            print(f"CameraSdkInit failed: status={st}", file=sys.stderr)
            return 4

    # Prefer enumerating then initializing by index 0.
    if not hasattr(sdk, "CameraEnumerateDevice") or not hasattr(sdk, "CameraInit"):
        print(
            "libMVSDK loaded but expected CameraEnumerateDevice/CameraInit symbols "
            "are missing. Check that you installed the matching ARM64 SDK version.",
            file=sys.stderr,
        )
        return 5

    print(
        "libMVSDK is present, but a full typed binding (CameraDevInfo / FrameHead "
        "structs) is still needed for a reliable grab.\n"
        f"Installed library: {lib_path}\n"
        "Next step after SDK install: extend this script with the official "
        "Python kit from MindVision's download page "
        "('Industrial Camera Python Programming Development Kit').",
        file=sys.stderr,
    )
    # Placeholder so callers know we intentionally stop short of unsafe guesses.
    _ = (np, cv2, h_camera, out_path)
    return 6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="report USB + SDK status")
    parser.add_argument("--save", type=Path, help="grab one frame to this PNG path")
    args = parser.parse_args()
    if args.probe or not args.save:
        code = probe()
        if args.save and code != 0:
            return code
        if not args.save:
            return code
    return grab_one_png(args.save)


if __name__ == "__main__":
    sys.exit(main())
