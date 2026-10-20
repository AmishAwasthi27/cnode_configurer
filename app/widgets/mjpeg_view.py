"""MJPEG stream viewer widget.

Connects to an mjpeg-streamer HTTP stream (as served by `cnode_handler.camera_streamer`)
and displays frames in a `QLabel`. A background thread parses multipart/x-mixed-replace
bodies into `QImage` objects delivered to the UI thread via a signal.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

log = logging.getLogger("MJPEGView")


@dataclass
class StreamSource:
    host: str
    port: int
    path: str  # e.g. "camera/camera1"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/{self.path.lstrip('/')}"


class _StreamWorker(QObject):
    frame_ready = Signal(QImage)
    connected = Signal(bool)
    stopped = Signal()

    def __init__(self, source: StreamSource, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._stop = False
        self._response = None

    def stop(self) -> None:
        self._stop = True
        response = self._response
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    def run(self) -> None:
        while not self._stop:
            try:
                with urllib.request.urlopen(self._source.url, timeout=1.0) as resp:
                    self._response = resp
                    content_type = resp.headers.get("Content-Type", "")
                    boundary = self._parse_boundary(content_type)
                    if boundary is None:
                        log.warning("No boundary in stream %s", self._source.url)
                        self.connected.emit(False)
                        self._stop_wait(2.0)
                        continue
                    self.connected.emit(True)
                    self._consume_stream(resp, boundary)
                    self._response = None
            except (urllib.request.URLError, ConnectionError, socket.timeout, OSError) as exc:
                self._response = None
                log.info("Stream %s offline: %s", self._source.url, exc)
                self.connected.emit(False)
                self._stop_wait(2.0)
            except Exception as exc:  # pragma: no cover
                self._response = None
                log.warning("Stream %s error: %s", self._source.url, exc)
                self.connected.emit(False)
                self._stop_wait(2.0)
        self.stopped.emit()

    def _stop_wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            time.sleep(min(0.1, deadline - time.monotonic()))

    @staticmethod
    def _parse_boundary(content_type: str) -> Optional[bytes]:
        parts = [p.strip() for p in content_type.split(";")]
        for part in parts:
            if part.lower().startswith("boundary="):
                value = part.split("=", 1)[1].strip().strip('"')
                if not value.startswith("--"):
                    value = "--" + value
                return value.encode("latin-1")
        return None

    def _consume_stream(self, resp, boundary: bytes) -> None:
        buffer = b""
        while not self._stop:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                bidx = buffer.find(boundary)
                if bidx < 0:
                    break
                header_end = buffer.find(b"\r\n\r\n", bidx)
                if header_end < 0:
                    break
                headers_raw = buffer[bidx:header_end].decode("latin-1", errors="ignore")
                content_length = self._extract_length(headers_raw)
                if content_length is None:
                    buffer = buffer[bidx + len(boundary):]
                    continue
                body_start = header_end + 4
                body_end = body_start + content_length
                if len(buffer) < body_end:
                    break
                jpeg = buffer[body_start:body_end]
                buffer = buffer[body_end:]
                image = QImage.fromData(jpeg, "JPG")
                if not image.isNull():
                    self.frame_ready.emit(image)

    @staticmethod
    def _extract_length(headers_raw: str) -> Optional[int]:
        for line in headers_raw.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip().lower() == "content-length":
                    try:
                        return int(v.strip())
                    except ValueError:
                        return None
        return None


class MJPEGView(QWidget):
    connected = Signal(bool)
    frame_received = Signal(QImage)  # emitted for every decoded frame

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(360)
        self._label = QLabel("No stream")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setMinimumHeight(360)
        self._label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._label.setStyleSheet(
            "background: #0F172A; color: #94A3B8; font-size: 11pt;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self._thread: Optional[QThread] = None
        self._worker: Optional[_StreamWorker] = None
        self._source: Optional[StreamSource] = None
        self._current_image: Optional[QImage] = None
        # Keep only the newest decoded frame. Queuing every camera frame to the
        # GUI can build a stale backlog while widgets or inference are busy.
        self._pending_lock = threading.Lock()
        self._pending_image: Optional[QImage] = None
        self._pending_sequence = 0
        self._displayed_sequence = 0
        self._present_timer = QTimer(self)
        self._present_timer.setInterval(33)
        self._present_timer.timeout.connect(self._present_latest_frame)
        self._present_timer.start()

    def current_image(self) -> Optional[QImage]:
        return QImage(self._current_image) if self._current_image is not None else None

    def displayed_rect(self):
        """Return the QRect inside this widget that actually holds the scaled image."""
        if self._current_image is None:
            return None
        img_size = self._current_image.size()
        label_size = self._label.size()
        scaled = img_size.scaled(label_size, Qt.KeepAspectRatio)
        x = (label_size.width() - scaled.width()) // 2
        y = (label_size.height() - scaled.height()) // 2
        from PySide6.QtCore import QRect
        return QRect(self._label.pos().x() + x, self._label.pos().y() + y,
                     scaled.width(), scaled.height())

    def set_source(self, source: Optional[StreamSource]) -> None:
        old_url = self._source.url if self._source is not None else None
        new_url = source.url if source is not None else None
        if old_url == new_url:
            if source is not None and (self._thread is None or not self._thread.isRunning()):
                self.start()
            return
        self.stop()
        # Source really changed — drop the retained frame so we don't show a
        # stale image from the previous source.
        self._current_image = None
        with self._pending_lock:
            self._pending_image = None
            self._pending_sequence = 0
            self._displayed_sequence = 0
        self._label.setPixmap(QPixmap())
        self._label.setText("No stream")
        self._source = source
        if source is not None:
            self.start()

    def start(self) -> None:
        if self._source is None or self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = _StreamWorker(self._source)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # This slot only writes a thread-safe mailbox; all widget work remains
        # on the GUI thread in _present_latest_frame().
        self._worker.frame_ready.connect(
            self._store_latest_frame, Qt.ConnectionType.DirectConnection
        )
        self._worker.connected.connect(self.connected.emit)
        # QThread.quit() is thread-safe. A direct connection avoids queuing the
        # quit request to the GUI thread while that thread is waiting in stop().
        self._worker.stopped.connect(
            self._thread.quit, Qt.ConnectionType.DirectConnection
        )
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._label.setText(f"Connecting to {self._source.url}…")

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            if self._thread.isRunning():
                log.warning("MJPEG worker did not stop promptly for %s", self._source.url if self._source else "unknown source")
            self._thread = None
            self._worker = None
        # Keep the last decoded frame so consumers (e.g. template capture) can
        # still read `current_image()` while the stream is paused.
        if self._current_image is None:
            self._label.setPixmap(QPixmap())
            self._label.setText("No stream")

    @Slot(QImage)
    def _store_latest_frame(self, image: QImage) -> None:
        with self._pending_lock:
            self._pending_image = image
            self._pending_sequence += 1

    @Slot()
    def _present_latest_frame(self) -> None:
        with self._pending_lock:
            if self._pending_image is None or self._pending_sequence == self._displayed_sequence:
                return
            image = self._pending_image
            self._displayed_sequence = self._pending_sequence
        self._current_image = QImage(image)
        self.frame_received.emit(self._current_image)
        self._repaint()

    def resizeEvent(self, event) -> None:  # noqa: D401, N802
        super().resizeEvent(event)
        self._repaint()

    def _repaint(self) -> None:
        if self._current_image is None:
            return
        pix = QPixmap.fromImage(self._current_image).scaled(
            self._label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._label.setPixmap(pix)

    def closeEvent(self, event) -> None:  # noqa: D401, N802
        self.stop()
        super().closeEvent(event)
