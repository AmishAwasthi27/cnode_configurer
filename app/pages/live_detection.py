"""Local live category detection and registered-object visual matching."""
from __future__ import annotations

import logging
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.constants import ROOT_DIR
from app.detection.store import ReferenceStore
from app.detection.vision import Detection, ReferenceMatcher, YoloXDetector
from app.managers import connection_manager
from app.managers.local_manager import local_manager
from app.pages._common import PageBase, PageHeader, primary_button, secondary_button
from app.widgets.card import Card
from app.widgets.mjpeg_view import MJPEGView, StreamSource

log = logging.getLogger("LiveDetectionPage")

MODEL_PATH = ROOT_DIR / "models" / "object_detection_yolox_2022nov_int8.onnx"
REFERENCE_ROOT = ROOT_DIR / "data" / "detection_references"
PROFILE_PATH = ROOT_DIR / "data" / "local_detection_profile.json"


def _qimage_to_bgr(image: QImage) -> np.ndarray:
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    raw = np.frombuffer(rgb.constBits(), dtype=np.uint8, count=rgb.sizeInBytes())
    raw = raw.reshape(rgb.height(), rgb.bytesPerLine())[:, : rgb.width() * 3]
    return cv2.cvtColor(raw.reshape(rgb.height(), rgb.width(), 3), cv2.COLOR_RGB2BGR)


class _OverlayView(MJPEGView):
    """MJPEGView that draws local inference results over its current frame."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._detections: list[Detection] = []

    def set_detections(self, detections: list[Detection]) -> None:
        self._detections = list(detections)
        self._repaint()

    def _repaint(self) -> None:
        image = self._current_image
        if image is None:
            return
        size = self._label.size()
        pix = QPixmap.fromImage(image).scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if not self._detections:
            self._label.setPixmap(pix)
            return
        scale_x, scale_y = pix.width() / image.width(), pix.height() / image.height()
        painter = QPainter(pix)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        for det in self._detections:
            color = QColor("#22c55e" if det.object_name else "#f59e0b")
            pen = QPen(color, 2)
            painter.setPen(pen)
            x1, y1 = round(det.x1 * scale_x), round(det.y1 * scale_y)
            x2, y2 = round(det.x2 * scale_x), round(det.y2 * scale_y)
            painter.drawRect(x1, y1, x2 - x1, y2 - y1)
            identity = det.object_name or det.identity_status
            if det.identity_status in {"Template match", "No template match"}:
                identity = f"{identity} {det.match_score:.2f}"
            label = f"{det.category} {det.confidence:.0%} · {identity}"
            painter.fillRect(x1, max(0, y1 - 23), min(pix.width() - x1, len(label) * 7 + 10), 21, color)
            painter.setPen(QColor("#111827"))
            painter.drawText(x1 + 5, max(15, y1 - 7), label)
        painter.end()
        self._label.setPixmap(pix)


class _InferenceWorker(QObject):
    initialized = Signal()
    result_ready = Signal(object, object)  # source BGR frame, detections
    failed = Signal(str)

    def __init__(self, model_path: Path) -> None:
        super().__init__()
        self.model_path = model_path
        self.detector: Optional[YoloXDetector] = None
        self.references: list[dict] = []
        self.matcher = ReferenceMatcher([])
        self.confidence = 0.8
        self.match_threshold = 0.32
        self.categories: set[str] = set()

    @Slot()
    def initialize(self) -> None:
        try:
            self.detector = YoloXDetector(self.model_path, confidence=self.confidence)
        except Exception as exc:
            self.failed.emit(f"Could not load category detector: {exc}")
            return
        self.initialized.emit()

    @Slot(object)
    def set_references(self, refs: object) -> None:
        self.references = list(refs) if isinstance(refs, (tuple, list)) else []
        self.matcher = ReferenceMatcher(self.references)

    @Slot(float)
    def set_confidence(self, value: float) -> None:
        self.confidence = value

    @Slot(float)
    def set_match_threshold(self, value: float) -> None:
        self.match_threshold = value

    @Slot(str)
    def set_categories(self, value: str) -> None:
        self.categories = {item.strip().casefold() for item in value.split(",") if item.strip()}

    @Slot(object)
    def infer(self, qimage: object) -> None:
        try:
            if self.detector is None or not isinstance(qimage, QImage):
                return
            frame = _qimage_to_bgr(qimage)
            # The category detector proposes candidate boxes; the saved
            # template is the actual acceptance test below. Keep proposals
            # permissive so a banana can still reach template matching when
            # YOLO's category score is below the UI confidence setting.
            detections = self.detector.detect(frame, min(self.confidence, 0.15))
            workflow = local_manager.active_workflow()
            local_profiles = local_manager.list_profiles(workflow.id)
            template_profiles = [p for p in local_profiles if p.algorithm == "template_matching" and local_manager.list_templates(p.id)]
            profile = next((p for p in template_profiles if p.is_default), template_profiles[0] if template_profiles else None)
            # Template names such as "banana1" identify the intended COCO
            # category. Restrict detections to those categories before
            # comparing crops; otherwise unrelated objects can score against
            # a template and appear as random matches.
            expected_categories = (
                {category.casefold() for category in local_manager.template_categories(profile.id)}
                if profile is not None else set()
            )
            # Only filter by categories inferred from this profile's templates.
            # An empty set means no category filter; the legacy UI default
            # ("bottle") must not hide other model proposals such as bananas.
            category_filter = expected_categories or None
            if category_filter:
                detections = [item for item in detections if item.category.casefold() in category_filter]
            resolved = []
            for detection in detections:
                crop = frame[detection.y1:detection.y2, detection.x1:detection.x2]
                if profile is not None:
                    name, score = local_manager.match_template(profile.id, crop, category=detection.category)
                    if not name:
                        continue
                    status = "Template match"
                else:
                    status, name, score = self.matcher.match(
                        detection.category, crop,
                        threshold=self.match_threshold,
                    )
                    # Live Detection reports only objects that pass visual
                    # identity matching, never every category-level proposal.
                    if not name:
                        continue
                resolved.append(replace(detection, identity_status=status, object_name=name, match_score=score))
            self.result_ready.emit(frame, resolved)
        except Exception as exc:  # keep the camera UI alive on a bad frame/model result
            self.failed.emit(f"Live inference failed: {exc}")


class LiveDetectionPage(PageBase):
    """Run inference against the existing cnode MJPEG camera stream."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self.status_label = QLabel("Connect to a device or localhost camera, then start detection.")
        self.status_label.setObjectName("Muted")
        self.start_btn = primary_button("  Start live detection", "detect")
        self.start_btn.clicked.connect(self._toggle_detection)
        controls = QHBoxLayout()
        controls.addWidget(self.status_label, 1)
        controls.addWidget(self.start_btn)
        header = PageHeader("Live Detection", right_widget=self._wrap(controls), icon_name="detect")
        super().__init__(header, parent)

        self.store = ReferenceStore(REFERENCE_ROOT)
        self.camera_id_edit = QLineEdit("camera1")
        self.camera_id_edit.setMaximumWidth(160)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 0.9)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setValue(0.8)
        self.match_spin = QDoubleSpinBox()
        self.match_spin.setRange(0.1, 0.9)
        self.match_spin.setSingleStep(0.02)
        self.match_spin.setValue(0.32)
        self.categories_edit = QLineEdit()
        self.categories_edit.setPlaceholderText("bottle")
        self.categories_edit.setText("bottle")
        self.register_name = QLineEdit()
        self.register_name.setPlaceholderText("e.g. Red bottle A")
        self.register_btn = secondary_button("  Register selected detection", "save")
        self.register_btn.clicked.connect(self.register_selected)
        panel = Card(title="Detection settings", icon_name="settings")
        form = QFormLayout()
        form.addRow("Camera ID", self.camera_id_edit)
        form.addRow("Category confidence", self.confidence_spin)
        form.addRow("Visual match threshold", self.match_spin)
        form.addRow("Category filter", self.categories_edit)
        form.addRow("Object name / add another reference", self.register_name)
        panel.body().addLayout(form)
        panel.body().addWidget(self.register_btn)

        self.view = _OverlayView()
        self.view.setMinimumHeight(480)
        self.results = QListWidget()
        self.results.setMinimumWidth(330)
        result_card = Card(title="Live results", icon_name="detect")
        result_card.add(self.results)
        row = QHBoxLayout()
        row.addWidget(self.view, 3)
        row.addWidget(result_card, 2)
        self.content().addLayout(row, 1)
        self.content().addWidget(panel)

        self._thread = QThread(self)
        self._worker = _InferenceWorker(MODEL_PATH)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.initialize)
        self._worker.initialized.connect(self._on_model_ready)
        self._worker.result_ready.connect(self._on_result)
        self._worker.failed.connect(self._on_error)
        self._bridge = _FrameBridge()
        self._bridge.frame.connect(self._worker.infer, Qt.ConnectionType.QueuedConnection)
        self._refs_bridge = _ReferencesBridge()
        self._refs_bridge.changed.connect(self._worker.set_references, Qt.ConnectionType.QueuedConnection)
        self._conf_bridge = _FloatBridge()
        self._conf_bridge.changed.connect(self._worker.set_confidence, Qt.ConnectionType.QueuedConnection)
        self._match_bridge = _FloatBridge()
        self._match_bridge.changed.connect(self._worker.set_match_threshold, Qt.ConnectionType.QueuedConnection)
        self._categories_bridge = _StringBridge()
        self._categories_bridge.changed.connect(self._worker.set_categories, Qt.ConnectionType.QueuedConnection)
        self.view.frame_received.connect(self._on_frame)
        self.confidence_spin.valueChanged.connect(self._conf_bridge.changed.emit)
        self.match_spin.valueChanged.connect(self._match_bridge.changed.emit)
        self.categories_edit.textChanged.connect(self._categories_bridge.changed.emit)
        self._busy_lock = threading.Lock()
        self._busy = False
        self._model_ready = False
        self._want_running = False
        self._last_frame: Optional[np.ndarray] = None
        self._last_detections: list[Detection] = []
        self._fps_started = time.monotonic()
        self._fps_frames = 0
        connection_manager.connected_changed.connect(self._on_connection_changed)
        connection_manager.connection_changed.connect(lambda _cfg: self._set_stream_if_ready())
        self._load_profile()
        self.confidence_spin.valueChanged.connect(lambda _v: self._save_profile())
        self.match_spin.valueChanged.connect(lambda _v: self._save_profile())
        self.categories_edit.textChanged.connect(lambda _v: self._save_profile())
        self._refs_bridge.changed.emit(self.store.references())
        self._thread.start()

    @staticmethod
    def _wrap(layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _load_profile(self) -> None:
        try:
            settings = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.confidence_spin.setValue(float(settings.get("confidence", 0.8)))
        self.match_spin.setValue(float(settings.get("visual_match_threshold", 0.32)))
        self.categories_edit.setText(str(settings.get("categories", "bottle")))

    def _save_profile(self) -> None:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        settings = {
            "confidence": self.confidence_spin.value(),
            "visual_match_threshold": self.match_spin.value(),
            "categories": self.categories_edit.text(),
        }
        temporary = PROFILE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        temporary.replace(PROFILE_PATH)

    def start_detection(self) -> None:
        self._want_running = True
        self.start_btn.setText("  Stop live detection")
        if not connection_manager.connected:
            try:
                connection_manager.connect()
            except RuntimeError as exc:
                self._want_running = False
                self.start_btn.setText("  Start live detection")
                self._on_error(str(exc))
                return
        self._set_stream_if_ready()
        if self._model_ready:
            self.status_label.setText("Live detection running")
        else:
            self.status_label.setText("Loading category model…")

    def _toggle_detection(self) -> None:
        if self._want_running:
            self._want_running = False
            self.view.set_source(None)
            self.start_btn.setText("  Start live detection")
            self.status_label.setText("Detection stopped")
            return
        self.start_detection()
        if self._want_running:
            self.start_btn.setText("  Stop live detection")

    def _on_connection_changed(self, connected: bool) -> None:
        if connected and self._want_running:
            self._set_stream_if_ready()
        elif not connected:
            self.view.set_source(None)

    def _set_stream_if_ready(self) -> None:
        if not self._want_running or not connection_manager.connected:
            return
        cfg = connection_manager.config
        camera_id = self.camera_id_edit.text().strip() or "camera1"
        host = cfg.host
        if host.strip().lower() in {"localhost", "::1"}:
            # LocalCameraServer binds IPv4 loopback; avoid localhost resolving
            # to IPv6 first on Windows, where the stream endpoint is absent.
            host = "127.0.0.1"
        self.view.set_source(StreamSource(host, cfg.camera_stream_port, f"camera/{camera_id}"))

    @Slot()
    def _on_model_ready(self) -> None:
        self._model_ready = True
        if self._want_running:
            self.status_label.setText("Live detection running · category model ready")

    @Slot(object)
    def _on_frame(self, image: QImage) -> None:
        if not self._model_ready or not self._want_running:
            return
        self.start_btn.setText("  Stop live detection")
        self._fps_frames += 1
        now = time.monotonic()
        elapsed = now - self._fps_started
        if elapsed >= 1.0:
            fps = self._fps_frames / elapsed
            self._fps_started = now
            self._fps_frames = 0
            self.status_label.setText(f"Live detection running · {fps:.0f} camera FPS")
        with self._busy_lock:
            if self._busy:
                return
            self._busy = True
        self._bridge.frame.emit(QImage(image))

    @Slot(object, object)
    def _on_result(self, frame: object, detections: object) -> None:
        with self._busy_lock:
            self._busy = False
        if not isinstance(frame, np.ndarray):
            return
        self._last_frame = frame
        self._last_detections = list(detections)
        self.view.set_detections(self._last_detections)
        if not self._last_detections:
            self.results.clear()
            self.results.addItem("No supported objects detected in this frame.")
        else:
            selected = self.results.currentRow()
            while self.results.count() > len(self._last_detections):
                self.results.takeItem(self.results.count() - 1)
            for index, item in enumerate(self._last_detections):
                identity = item.object_name or item.identity_status
                detail = f"{identity} · template {item.match_score:.2f}" if item.identity_status in {"Template match", "No template match"} else identity
                text = f"{item.category} · {item.confidence:.0%}\n{detail}"
                if index < self.results.count():
                    row = self.results.item(index)
                    row.setText(text)
                else:
                    row = QListWidgetItem(text)
                    self.results.addItem(row)
                row.setData(Qt.ItemDataRole.UserRole, index)
            if selected >= 0:
                self.results.setCurrentRow(min(selected, len(self._last_detections) - 1))

    @Slot(str)
    def _on_error(self, message: str) -> None:
        with self._busy_lock:
            self._busy = False
        self.status_label.setText(message)
        log.warning(message)

    def register_selected(self) -> None:
        row = self.results.currentItem()
        if row is None or row.data(Qt.ItemDataRole.UserRole) is None:
            QMessageBox.information(self, "Select a detection", "Select an object from the live results first.")
            return
        if self._last_frame is None:
            QMessageBox.information(self, "No camera frame", "Wait for a live camera frame first.")
            return
        detection = self._last_detections[int(row.data(Qt.ItemDataRole.UserRole))]
        name = self.register_name.text().strip()
        if not name:
            name, ok = QInputDialog.getText(self, "Register object", f"Name this {detection.category}:")
            if not ok:
                return
            name = name.strip()
        if not name:
            return
        crop = self._last_frame[detection.y1:detection.y2, detection.x1:detection.x2].copy()
        try:
            item = self.store.add_reference(name, detection.category, crop)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Registration failed", str(exc))
            return
        self.register_name.setText(item["name"])
        self._refs_bridge.changed.emit(self.store.references())
        QMessageBox.information(self, "Reference saved", f"Saved another visual reference for <b>{item['name']}</b> ({detection.category}).")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._set_stream_if_ready()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.view.set_source(None)
        super().hideEvent(event)

    def shutdown(self) -> None:
        self._want_running = False
        self.view.set_source(None)
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)


class _FrameBridge(QObject):
    frame = Signal(object)


class _ReferencesBridge(QObject):
    changed = Signal(object)


class _FloatBridge(QObject):
    changed = Signal(float)


class _StringBridge(QObject):
    changed = Signal(str)

