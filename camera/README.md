# Camera capture (MindVision HT-SUA134GM)

Current table camera: **MindVision HT-SUA134GM-T1V-C** (USB3 industrial,
vendor `0xf622`, product `SUA134GM`). Azure Kinect support remains in the
repo root scripts (`kinect_web_control.py`, etc.) for when that sensor is
available.

## Why OpenCV alone is not enough

This camera does **not** enumerate as `/dev/video*`. It needs MindVision's
`libMVSDK.so` (linuxSDK ARM64). A plain `cv2.VideoCapture(0)` will fail.

## Install the ARM64 SDK on the Jetson

Official tarball (example): `linuxSDK_V2.1.0.49(202602041120).tar.gz` from
[MindVision Software Download](https://www.mindvision.ltd/Service-Support/Software-Download.html).

```bash
cd ~/Downloads/mindvision
tar -xzf linuxSDK_V2.1.0.49*.tar.gz
sudo cp include/* /usr/include/
sudo cp lib/arm64/libMVSDK.so /usr/lib/
sudo cp 88-mvusb.rules 99-mvusb.rules /etc/udev/rules.d/
sudo ldconfig
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Also install repo udev rules:

```bash
sudo cp ~/tiltytable/udev/99-tiltytable-mindvision.rules /etc/udev/rules.d/
```

`camera/mvsdk.py` is the official Python binding copied from
`demo/python_demo/mvsdk.py` in that SDK. Version recorded in `SDK_VERSION.txt`.

## Usage

```bash
cd ~/tiltytable && . .venv/bin/activate
python3 camera/mindvision_capture.py --probe
python3 camera/mindvision_capture.py --save /tmp/sua134.png
python3 camera/mindvision_capture.py --save /tmp/sua134.png --exposure-ms 10
```

## Kinect

When the Azure Kinect is connected, use the existing root scripts
(`live_capture_viewer.py`, `kinect_web_control.py`, …). Do not assume Kinect
APIs work for the MindVision camera.
