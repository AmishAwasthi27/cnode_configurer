"""Expose the PC webcam as the local camera MJPEG stream."""
from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import cv2
from app.managers.local_camera_config import local_camera_config

log = logging.getLogger("LocalCameraServer")
_active_server = None


def configure_local_camera(camera_id: str | None = None) -> None:
    cameras = local_camera_config.list()
    selected = camera_id or next(iter(cameras), None)
    if _active_server is not None:
        _active_server.configure(cameras.get(selected, {"source": 0}) if selected else {"source": 0})


class _StreamingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    source: LocalCameraServer


class _StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not urlsplit(self.path).path.startswith("/camera/"):
            self.send_error(404)
            return

        source = self.server.source
        frame = source.read_jpeg()
        if frame is None:
            self.send_error(503, "No frame available from the PC webcam")
            return

        try:
            self.send_response(200)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            while source.running:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                time.sleep(1 / 15)
                frame = source.read_jpeg()
                if frame is None:
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, _format: str, *args: object) -> None:
        return


class LocalCameraServer:
    """Serve the default Windows webcam as an MJPEG stream on loopback."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8060) -> None:
        global _active_server
        _active_server = self
        self.host = host
        self.port = port
        self._httpd: _StreamingServer | None = None
        self._thread: threading.Thread | None = None
        self._capture_thread: threading.Thread | None = None
        self._frame_lock = threading.Lock()
        self._latest_frame: bytes | None = None
        self._running = threading.Event()
        self._stop_event = threading.Event()
        self._reconfigure_event = threading.Event()
        self._camera_config: dict = {"source": 0, "frame_width": 1280, "frame_height": 720}

    def configure(self, config: dict | None) -> None:
        """Apply local capture settings; the stream restarts its capture loop."""
        with self._frame_lock:
            self._camera_config = dict(config or {"source": 0})
            self._latest_frame = None
        if self.running:
            self._reconfigure_event.set()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        try:
            server = _StreamingServer((self.host, self.port), _StreamHandler)
        except OSError as exc:
            log.info("Local camera feed not started on %s:%s: %s", self.host, self.port, exc)
            return False

        server.source = self
        configure_local_camera()
        self._httpd = server
        self._running.set()
        self._stop_event.clear()
        self._reconfigure_event.clear()
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="local-camera-http",
            daemon=True,
        )
        self._thread.start()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="local-webcam-capture",
            daemon=True,
        )
        self._capture_thread.start()
        log.info("Local webcam stream available at http://%s:%s/camera/camera1", self.host, self.port)
        return True

    def stop(self) -> None:
        self._running.clear()
        self._stop_event.set()
        server, self._httpd = self._httpd, None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        capture_thread, self._capture_thread = self._capture_thread, None
        if capture_thread is not None:
            capture_thread.join(timeout=2)
        with self._frame_lock:
            self._latest_frame = None

    def read_jpeg(self) -> bytes | None:
        with self._frame_lock:
            return self._latest_frame

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
            capture = None
            stalled = False
            try:
                with self._frame_lock:
                    config = dict(self._camera_config)
                source = config.get("source", 0)
                source = int(source) if str(source).lstrip("+-").isdigit() else str(source)
                capture = cv2.VideoCapture(source, cv2.CAP_DSHOW) if isinstance(source, int) else cv2.VideoCapture(source)
                if isinstance(source, int) and not capture.isOpened():
                    capture.release()
                    capture = cv2.VideoCapture(source)
                if not capture.isOpened():
                    log.warning("Could not open local camera source %s", source)
                else:
                    # Keep the driver's queue shallow so reads return recent
                    # webcam frames instead of draining an old buffered frame
                    # sequence after the UI or detector falls behind.
                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(config.get("frame_width", 1280)))
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(config.get("frame_height", 720)))
                    previous_frame = None
                    unchanged_since = time.monotonic()
                    while not self._stop_event.is_set() and not self._reconfigure_event.is_set():
                        ok, image = capture.read()
                        if not ok:
                            break
                        if isinstance(source, int):
                            if previous_frame is not None and cv2.norm(image, previous_frame, cv2.NORM_INF) == 0:
                                if time.monotonic() - unchanged_since >= 2.0:
                                    log.warning("Local camera source %s stopped producing new frames; reopening it", source)
                                    stalled = True
                                    break
                            else:
                                previous_frame = image.copy()
                                unchanged_since = time.monotonic()
                        if config.get("mirror_h"):
                            image = cv2.flip(image, 1)
                        if config.get("mirror_v"):
                            image = cv2.flip(image, 0)
                        rotation = int(config.get("rotate_deg", 0)) % 360
                        if rotation == 90:
                            image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                        elif rotation == 180:
                            image = cv2.rotate(image, cv2.ROTATE_180)
                        elif rotation == 270:
                            image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
                        encoded, buffer = cv2.imencode(".jpg", image)
                        if encoded:
                            with self._frame_lock:
                                self._latest_frame = buffer.tobytes()
                        self._stop_event.wait(1 / 15)
            except Exception:
                log.exception("Error reading the PC webcam")
            finally:
                if capture is not None:
                    capture.release()
                with self._frame_lock:
                    self._latest_frame = None
            if self._reconfigure_event.is_set():
                self._reconfigure_event.clear()
                continue
            if not self._stop_event.is_set():
                self._stop_event.wait(0.2 if stalled else 2)
