#!/usr/bin/env python3
import argparse
import os
import sys

import cv2
import numpy as np
from pyk4a import (
    ColorResolution,
    Config,
    DepthMode,
    FPS,
    ImageFormat,
    K4AException,
    K4ATimeoutException,
    PyK4A,
    connected_device_count,
)


COLOR_RESOLUTIONS = {
    "720p": ColorResolution.RES_720P,
    "1080p": ColorResolution.RES_1080P,
    "1440p": ColorResolution.RES_1440P,
    "1536p": ColorResolution.RES_1536P,
    "2160p": ColorResolution.RES_2160P,
    "3072p": ColorResolution.RES_3072P,
}

DEPTH_MODES = {
    "nfov_2x2binned": DepthMode.NFOV_2X2BINNED,
    "nfov_unbinned": DepthMode.NFOV_UNBINNED,
    "wfov_2x2binned": DepthMode.WFOV_2X2BINNED,
    "wfov_unbinned": DepthMode.WFOV_UNBINNED,
}

FPS_VALUES = {
    "5": FPS.FPS_5,
    "15": FPS.FPS_15,
    "30": FPS.FPS_30,
}

DEPTH_ENGINE_DISPLAY = ":0"
TOOLTIP_OFFSET = (12, 18)
TOOLTIP_PADDING = (8, 6)
TOOLTIP_FONT_SCALE = 0.55
TOOLTIP_FONT_THICKNESS = 1


def parse_args():
    parser = argparse.ArgumentParser(
        description="Azure Kinect viewer. Press Space to grab and display one fresh color/depth frame."
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=0,
        help="Azure Kinect device index",
    )
    parser.add_argument(
        "--color-resolution",
        choices=sorted(COLOR_RESOLUTIONS),
        default="720p",
        help="Color camera resolution",
    )
    parser.add_argument(
        "--depth-mode",
        choices=sorted(DEPTH_MODES),
        default="nfov_unbinned",
        help="Depth camera mode",
    )
    parser.add_argument(
        "--fps",
        choices=sorted(FPS_VALUES, key=int),
        default="30",
        help="Camera frame rate",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=4000,
        help="Depth display range in millimeters",
    )
    parser.add_argument(
        "--resize-width",
        type=int,
        default=640,
        help="Display width for each pane; use 0 for native size",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=1000,
        help="Capture timeout in milliseconds",
    )
    parser.add_argument(
        "--aligned-depth",
        action="store_true",
        help="Display depth transformed into the color camera view",
    )
    args = parser.parse_args()
    if args.max_depth <= 0:
        parser.error("--max-depth must be greater than 0")
    return args


def set_display(display, role, quiet=False):
    if display:
        os.environ["DISPLAY"] = display
        if not quiet:
            print(f"Using X display {display} for {role}.")


def color_to_bgr(color):
    if color is None:
        return None

    if color.ndim == 1:
        return cv2.imdecode(color, cv2.IMREAD_COLOR)
    if color.ndim == 2:
        return cv2.cvtColor(color, cv2.COLOR_GRAY2BGR)
    if color.ndim == 3 and color.shape[-1] == 4:
        return cv2.cvtColor(color, cv2.COLOR_BGRA2BGR)
    if color.ndim == 3 and color.shape[-1] == 3:
        return color

    raise ValueError(f"Unsupported color image shape: {color.shape}")


def depth_to_display(depth_mm, max_depth_mm):
    depth = depth_mm.astype(np.float32, copy=False)
    invalid = ~np.isfinite(depth) | (depth <= 0)
    scaled = np.clip(depth, 0, max_depth_mm) * (255.0 / max_depth_mm)
    scaled[invalid] = 0
    scaled = scaled.astype(np.uint8)

    colormap = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)
    display = cv2.applyColorMap(scaled, colormap)
    display[invalid] = (0, 0, 0)
    return display


def resize_to_width(img, width, interpolation):
    if width <= 0:
        return img

    h, w = img.shape[:2]
    if w == width:
        return img

    scale = width / w
    return cv2.resize(
        img,
        (width, max(1, int(round(h * scale)))),
        interpolation=interpolation,
    )


def pad_to_height(img, height):
    h, w = img.shape[:2]
    if h >= height:
        return img

    pad = height - h
    if img.ndim == 2:
        return cv2.copyMakeBorder(img, 0, pad, 0, 0, cv2.BORDER_CONSTANT, value=0)
    return cv2.copyMakeBorder(img, 0, pad, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))


def labeled(img, label):
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 34), (0, 0, 0), thickness=-1)
    cv2.putText(
        out,
        label,
        (12, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return out


def make_view(color_bgr, depth_mm, max_depth_mm, resize_width, aligned_depth):
    color_display = resize_to_width(color_bgr, resize_width, cv2.INTER_AREA)
    depth_display = depth_to_display(depth_mm, max_depth_mm)
    depth_display = resize_to_width(depth_display, resize_width, cv2.INTER_NEAREST)
    depth_display_height, depth_display_width = depth_display.shape[:2]
    depth_lookup = {
        "x": color_display.shape[1],
        "y": 0,
        "width": depth_display_width,
        "height": depth_display_height,
        "source_width": depth_mm.shape[1],
        "source_height": depth_mm.shape[0],
    }

    height = max(color_display.shape[0], depth_display.shape[0])
    color_display = pad_to_height(color_display, height)
    depth_display = pad_to_height(depth_display, height)

    depth_label = "Depth aligned to color" if aligned_depth else "Depth"
    color_display = labeled(color_display, "Color")
    depth_display = labeled(depth_display, depth_label)
    return np.hstack((color_display, depth_display)), depth_lookup


def set_mouse_position(event, x, y, flags, hover):
    del flags
    if event in (cv2.EVENT_MOUSEMOVE, cv2.EVENT_LBUTTONDOWN):
        hover["position"] = (x, y)


def depth_sample_at(depth_mm, depth_lookup, position):
    if depth_mm is None or depth_lookup is None or position is None:
        return None

    x, y = position
    depth_x = x - depth_lookup["x"]
    depth_y = y - depth_lookup["y"]
    if (
        depth_x < 0
        or depth_y < 0
        or depth_x >= depth_lookup["width"]
        or depth_y >= depth_lookup["height"]
    ):
        return None

    source_x = min(
        depth_lookup["source_width"] - 1,
        int(depth_x * depth_lookup["source_width"] / depth_lookup["width"]),
    )
    source_y = min(
        depth_lookup["source_height"] - 1,
        int(depth_y * depth_lookup["source_height"] / depth_lookup["height"]),
    )
    return source_x, source_y, depth_mm[source_y, source_x]


def format_depth_sample(sample):
    if sample is None:
        return None

    x, y, value = sample
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        return f"x={x}, y={y} | No depth"
    return f"x={x}, y={y} | {int(round(value))} mm"


def draw_tooltip(img, position, text):
    if position is None or not text:
        return img

    out = img.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX
    pad_x, pad_y = TOOLTIP_PADDING
    (text_width, text_height), baseline = cv2.getTextSize(
        text,
        font,
        TOOLTIP_FONT_SCALE,
        TOOLTIP_FONT_THICKNESS,
    )
    box_width = text_width + pad_x * 2
    box_height = text_height + baseline + pad_y * 2

    x = position[0] + TOOLTIP_OFFSET[0]
    y = position[1] + TOOLTIP_OFFSET[1]
    if x + box_width >= out.shape[1]:
        x = position[0] - TOOLTIP_OFFSET[0] - box_width
    if y + box_height >= out.shape[0]:
        y = position[1] - TOOLTIP_OFFSET[1] - box_height

    x = max(0, min(x, out.shape[1] - box_width - 1))
    y = max(0, min(y, out.shape[0] - box_height - 1))
    box_end = (x + box_width, y + box_height)

    cv2.rectangle(out, (x, y), box_end, (24, 24, 24), thickness=-1)
    cv2.rectangle(out, (x, y), box_end, (235, 235, 235), thickness=1)
    cv2.putText(
        out,
        text,
        (x + pad_x, y + pad_y + text_height),
        font,
        TOOLTIP_FONT_SCALE,
        (255, 255, 255),
        TOOLTIP_FONT_THICKNESS,
        cv2.LINE_AA,
    )
    return out


def render_view(base_view, depth_mm, depth_lookup, hover_position):
    text = format_depth_sample(depth_sample_at(depth_mm, depth_lookup, hover_position))
    return draw_tooltip(base_view, hover_position, text)


def get_depth(capture, aligned_depth):
    if aligned_depth:
        return capture.transformed_depth
    return capture.depth


def get_latest_capture(k4a, timeout_ms):
    latest = k4a.get_capture(timeout=timeout_ms)

    while True:
        try:
            latest = k4a.get_capture(timeout=0)
        except K4ATimeoutException:
            return latest


def make_placeholder(width=1280, height=480):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        img,
        "Press Space to capture a frame",
        (32, height // 2 - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        "q or Esc quits",
        (32, height // 2 + 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (180, 180, 180),
        2,
        cv2.LINE_AA,
    )
    return img


def main():
    args = parse_args()
    viewer_display = os.environ.get("DISPLAY", "")
    print(
        f"Using depth engine display {DEPTH_ENGINE_DISPLAY} and "
        f"viewer display {viewer_display or '<unset>'}."
    )
    set_display(DEPTH_ENGINE_DISPLAY, "depth engine")

    device_count = connected_device_count()
    if device_count <= args.device_id:
        print(
            f"No Azure Kinect device at index {args.device_id}; found {device_count} device(s).",
            file=sys.stderr,
        )
        return 1

    config = Config(
        color_resolution=COLOR_RESOLUTIONS[args.color_resolution],
        color_format=ImageFormat.COLOR_BGRA32,
        depth_mode=DEPTH_MODES[args.depth_mode],
        camera_fps=FPS_VALUES[args.fps],
        synchronized_images_only=True,
    )

    k4a = PyK4A(config=config, device_id=args.device_id)
    window_name = "Azure Kinect Viewer - Space captures, q/Esc quits"

    print("Starting Azure Kinect viewer...")
    print(
        "Controls: Space = capture and display latest color/depth, "
        "hover depth pane = show mm value, q or Esc = quit"
    )

    try:
        k4a.start()
        set_display(viewer_display, "viewer")
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        hover = {"position": None}
        cv2.setMouseCallback(window_name, set_mouse_position, hover)
        base_view = make_placeholder()
        current_depth_mm = None
        depth_lookup = None
        cv2.imshow(window_name, base_view)

        while True:
            view = render_view(
                base_view,
                current_depth_mm,
                depth_lookup,
                hover["position"],
            )
            cv2.imshow(window_name, view)

            key = cv2.waitKey(50) & 0xFF
            if key in (27, ord("q")):
                break
            if key == 32:
                set_display(DEPTH_ENGINE_DISPLAY, "depth engine", quiet=True)
                try:
                    capture = get_latest_capture(k4a, args.timeout_ms)
                    color_bgr = color_to_bgr(capture.color)
                    depth_mm = get_depth(capture, args.aligned_depth)
                except K4ATimeoutException:
                    print("Timed out waiting for a camera frame.")
                    continue
                finally:
                    set_display(viewer_display, "viewer", quiet=True)

                if color_bgr is None or depth_mm is None:
                    print("Captured frame did not include both color and depth.")
                    continue

                current_depth_mm = depth_mm.copy()
                base_view, depth_lookup = make_view(
                    color_bgr,
                    current_depth_mm,
                    args.max_depth,
                    args.resize_width,
                    args.aligned_depth,
                )
                print("Displayed latest color/depth frame.")

            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    except (K4AException, RuntimeError, ValueError, cv2.error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        cv2.destroyAllWindows()
        if k4a.is_running:
            set_display(DEPTH_ENGINE_DISPLAY, "depth engine", quiet=True)
            k4a.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
