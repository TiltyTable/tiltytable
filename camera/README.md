# Camera capture (MindVision HT-SUA134GM)

Current table camera: **MindVision HT-SUA134GM-T1V-C** (USB3 industrial,
vendor `0xf622`, product `SUA134GM`). Azure Kinect support remains in the
repo root scripts (`kinect_web_control.py`, etc.) for when that sensor is
available.

## Why OpenCV alone is not enough

This camera does **not** enumerate as `/dev/video*`. It needs MindVision's
`libMVSDK.so` (linuxSDK). A plain `cv2.VideoCapture(0)` will fail.

## Install the ARM64 SDK on the Jetson

1. On a machine that can log into MindVision's site, download:
   - [Software Download](https://www.mindvision.ltd/Service-Support/Software-Download.html)
   - File: `linuxSDK_V2.1.0.49(...).tar.gz` (or newer)
2. Copy the tarball to the Jetson, e.g. `~/Downloads/mindvision/`.
3. Extract and install the **aarch64** library (not `x64`):

```bash
tar -xzf linuxSDK_*.tar.gz
cd linuxSDK_*   # exact folder name varies
# Confirm ARM64 lib exists:
find . -path '*aarch64*libMVSDK.so' -o -path '*arm64*libMVSDK.so'
sudo cp include/* /usr/include/
sudo cp <path-to-aarch64>/libMVSDK.so /usr/lib/
sudo ldconfig
```

4. Install USB udev rules from this repo:

```bash
sudo cp udev/99-tiltytable-mindvision.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

5. Reboot (MindVision's installer usually asks for this), then:

```bash
cd ~/tiltytable && . .venv/bin/activate
python3 camera/mindvision_capture.py --probe
python3 camera/mindvision_capture.py --save /tmp/sua134.png
```

## Status on this Jetson (as of last probe)

| Check | Result |
| --- | --- |
| USB present (`lsusb`) | Yes — `MindVision SUA134GM` |
| `/dev/video*` | No |
| `libMVSDK.so` aarch64 | **Not installed** (only an x86_64 mirror was available without login) |
| OpenCV capture | Fails (expected) |

Once the official ARM64 SDK is installed, `camera/mindvision_capture.py`
will open the camera via ctypes and grab frames.
