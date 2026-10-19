"""Read one live MJPEG frame from a running configurer and run the bundled detector."""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.detection.vision import YoloXDetector  # noqa: E402


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    camera_id = sys.argv[2] if len(sys.argv) > 2 else "camera1"
    url = f"http://{host}:8060/camera/{camera_id}"
    deadline = time.monotonic() + 18
    jpeg = bytearray()
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                while time.monotonic() < deadline:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    jpeg.extend(chunk)
                    start = jpeg.find(b"\xff\xd8")
                    end = jpeg.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start >= 0 and end >= 0:
                        frame = cv2.imdecode(np.frombuffer(jpeg[start : end + 2], np.uint8), cv2.IMREAD_COLOR)
                        if frame is None:
                            raise RuntimeError("The live endpoint returned an invalid JPEG frame.")
                        detector = YoloXDetector(ROOT / "models" / "object_detection_yolox_2022nov_int8.onnx")
                        detections = detector.detect(frame)
                        print(f"stream_frame={frame.shape[1]}x{frame.shape[0]}")
                        print("detections=" + ", ".join(f"{item.category}:{item.confidence:.2f}" for item in detections))
                        return 0
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.4)
    print(f"No JPEG frame received from {url}. Check camera access and keep Live Detection running.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
