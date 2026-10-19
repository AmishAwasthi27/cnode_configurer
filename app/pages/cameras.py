"""Cameras configuration page."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.constants import ROOT_DIR
from app.icons import icon
from app.managers import api_client, commander, connection_manager, local_camera_server
from app.managers.local_camera_config import local_camera_config
from app.pages._common import PageBase, PageHeader, primary_button, secondary_button
from app.widgets.card import Card
from app.widgets.mjpeg_view import MJPEGView, StreamSource

log = logging.getLogger("CamerasPage")


def _stream_host(host: str) -> str:
    return "127.0.0.1" if host.strip().lower() in {"localhost", "::1"} else host


TEMPLATES_DIR = ROOT_DIR / "templates"

try:
    from cnode_manager_sdk import CNodeManagerError
except ImportError:  # pragma: no cover
    CNodeManagerError = Exception  # type: ignore[assignment]


def _parse_source(text: str) -> Any:
    """Interpret the ``source`` text field the way cnode_handler expects.

    OpenCV / V4L accept either an integer device index (``0``, ``1``, ...)
    or a string URL / file path (``rtsp://...``, ``/dev/video0``, ``foo.mp4``).
    We preserve that duality: a bare signed integer becomes an ``int`` in the
    JSON config, anything else stays a ``str``. An empty box falls back to
    the default device index ``0`` so the camera still works.
    """
    raw = (text or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# Perspective picker dialog
# ---------------------------------------------------------------------------

class _PerspectivePicker(QDialog):
    """4-point perspective picker over a live MJPEG frame.

    Also lets the caller pick the ``dst_width`` / ``dst_height`` dimensions of
    the warped output so the full perspective config can round-trip through a
    single dialog.
    """

    def __init__(
        self,
        camera_id: str,
        initial_points: Optional[List[Tuple[int, int]]] = None,
        initial_dst: Optional[Tuple[int, int]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._camera_id = camera_id
        self.setWindowTitle(f"Perspective transform — {camera_id}")
        self.setModal(True)
        self.resize(900, 780)

        # Stored as frame/image coordinates (origin at camera frame top-left).
        self._points_frame: List[Tuple[int, int]] = [
            (int(x), int(y)) for (x, y) in (initial_points or [])
        ]

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        help_label = QLabel("Click 4 points on the frame to define the perspective region.")
        help_label.setObjectName("Muted")
        root.addWidget(help_label)

        self.view = MJPEGView()
        self.view.setMinimumHeight(520)
        if connection_manager.connected:
            cfg = connection_manager.config
            self.view.set_source(
                StreamSource(
                    host=_stream_host(cfg.host),
                    port=cfg.camera_stream_port,
                    path=f"camera/{camera_id}",
                )
            )
        self.view.installEventFilter(self)
        root.addWidget(self.view, 1)

        info = QHBoxLayout()
        self.points_label = QLabel()
        self._refresh_points_label()
        info.addWidget(self.points_label)
        info.addStretch(1)
        reset_btn = secondary_button("Reset", "refresh")
        reset_btn.clicked.connect(self._on_reset)
        info.addWidget(reset_btn)
        root.addLayout(info)

        dst_row = QHBoxLayout()
        dst_row.addWidget(QLabel("Output size:"))
        self.dst_w_spin = QSpinBox()
        self.dst_w_spin.setRange(16, 7680)
        self.dst_w_spin.setValue(int((initial_dst or (640, 480))[0]))
        self.dst_h_spin = QSpinBox()
        self.dst_h_spin.setRange(16, 4320)
        self.dst_h_spin.setValue(int((initial_dst or (640, 480))[1]))
        dst_row.addWidget(QLabel("width"))
        dst_row.addWidget(self.dst_w_spin)
        dst_row.addWidget(QLabel("height"))
        dst_row.addWidget(self.dst_h_spin)
        dst_row.addStretch(1)
        root.addLayout(dst_row)

        self.perspective_toggle_btn = secondary_button(
            "  Perspective Runtime: ON",
            "mode",
        )
        self.perspective_toggle_btn.setCheckable(True)
        self.perspective_toggle_btn.setChecked(True)
        self.perspective_toggle_btn.setEnabled(connection_manager.connected)
        self.perspective_toggle_btn.clicked.connect(self._toggle_perspective_runtime)
        root.addWidget(self.perspective_toggle_btn)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save_btn = btns.button(QDialogButtonBox.Save)
        save_btn.setText("Apply & Save")
        save_btn.setIcon(icon("save", color="#FFFFFF"))
        save_btn.setProperty("accent", True)
        save_btn.setIconSize(QSize(16, 16))
        cancel_btn = btns.button(QDialogButtonBox.Cancel)
        cancel_btn.setIcon(icon("close"))
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._overlay = _PointsOverlay(self.view, self._overlay_points)
        self._overlay.show()
        self._overlay.setGeometry(self.view.rect())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.view:
            if (
                isinstance(event, QMouseEvent)
                and event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
            ):
                frame_pt = self._to_frame_point(event.pos())
                if frame_pt is None:
                    return super().eventFilter(obj, event)
                if len(self._points_frame) >= 4:
                    self._points_frame.clear()
                self._points_frame.append(frame_pt)
                self._refresh_points_label()
                self._overlay.update()
            elif event.type() == QEvent.Resize:
                self._overlay.setGeometry(self.view.rect())
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.view.rect())

    def _on_reset(self) -> None:
        self._points_frame.clear()
        self._refresh_points_label()
        self._overlay.update()

    def _on_save(self) -> None:
        if len(self._points_frame) != 4:
            QMessageBox.warning(self, "Need 4 points", "Click exactly 4 points first.")
            return
        self.accept()

    def _toggle_perspective_runtime(self, checked: bool) -> None:
        if not connection_manager.connected:
            QMessageBox.information(
                self,
                "Not connected",
                "Connect to a cnode device first.",
            )
            self.perspective_toggle_btn.blockSignals(True)
            self.perspective_toggle_btn.setChecked(not checked)
            self.perspective_toggle_btn.blockSignals(False)
            return
        ok = commander.send(
            "calibration.set_camera_perspective",
            [self._camera_id, bool(checked)],
        )
        self.perspective_toggle_btn.setText(
            "  Perspective Runtime: ON" if checked else "  Perspective Runtime: OFF"
        )
        if not ok:
            QMessageBox.warning(
                self,
                "Command failed",
                "Could not send calibration.set_camera_perspective command.",
            )

    def _refresh_points_label(self) -> None:
        if not self._points_frame:
            self.points_label.setText("No points")
            return
        self.points_label.setText(
            " · ".join(f"({x}, {y})" for (x, y) in self._points_frame)
        )

    def points(self) -> List[Tuple[int, int]]:
        return list(self._points_frame)

    def dst_size(self) -> Tuple[int, int]:
        return (int(self.dst_w_spin.value()), int(self.dst_h_spin.value()))

    def _to_frame_point(self, view_pos: QPoint) -> Optional[Tuple[int, int]]:
        """Convert click in view/widget coords to underlying frame coords."""
        image = self.view.current_image()
        displayed = self.view.displayed_rect()
        if image is None or displayed is None or displayed.width() <= 0 or displayed.height() <= 0:
            return None
        if not displayed.contains(view_pos):
            return None
        sx = image.width() / displayed.width()
        sy = image.height() / displayed.height()
        fx = int((view_pos.x() - displayed.x()) * sx)
        fy = int((view_pos.y() - displayed.y()) * sy)
        fx = max(0, min(image.width() - 1, fx))
        fy = max(0, min(image.height() - 1, fy))
        return (fx, fy)

    def _overlay_points(self) -> List[QPoint]:
        """Convert stored frame coords to view/widget coords for drawing."""
        image = self.view.current_image()
        displayed = self.view.displayed_rect()
        if image is None or displayed is None or image.width() <= 0 or image.height() <= 0:
            return [QPoint(x, y) for (x, y) in self._points_frame]
        sx = displayed.width() / image.width()
        sy = displayed.height() / image.height()
        out: List[QPoint] = []
        for x, y in self._points_frame:
            vx = int(displayed.x() + x * sx)
            vy = int(displayed.y() + y * sy)
            out.append(QPoint(vx, vy))
        return out

    def done(self, result: int) -> None:  # noqa: D401
        # Shut the MJPEG worker down *before* the dialog is torn down so the
        # embedded QThread isn't destroyed while still running (which crashes
        # the whole process with "QThread: Destroyed while thread is still
        # running").
        try:
            self.view.stop()
        except Exception:  # pragma: no cover - defensive
            pass
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: D401, N802
        try:
            self.view.stop()
        except Exception:  # pragma: no cover
            pass
        super().closeEvent(event)


class _PointsOverlay(QWidget):
    def __init__(self, parent: QWidget, points_getter) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._points_getter = points_getter

    def paintEvent(self, event) -> None:  # noqa: N802
        points = self._points_getter() if callable(self._points_getter) else []
        if not points:
            return
        painter = QPainter(self)
        pen = QPen(QColor("#E0A800"))
        pen.setWidth(3)
        painter.setPen(pen)
        painter.setBrush(QColor(224, 168, 0, 80))
        for idx, point in enumerate(points, 1):
            painter.drawEllipse(point, 6, 6)
            painter.drawText(point + QPoint(8, -6), str(idx))
        if len(points) >= 2:
            for i in range(len(points) - 1):
                painter.drawLine(points[i], points[i + 1])
            if len(points) == 4:
                painter.drawLine(points[-1], points[0])


# ---------------------------------------------------------------------------
# Template capture dialog
# ---------------------------------------------------------------------------

class _TemplateCapture(QDialog):
    """Draw a bounding box on a frozen frame and save the cropped region."""

    def __init__(self, camera_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Capture template — {camera_id}")
        self.setModal(True)
        self.resize(980, 760)
        self._camera_id = camera_id
        self._origin: Optional[QPoint] = None
        self._current_rect: QRect = QRect()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(
            "Click and drag on the live view to select a region. Then enter a name and "
            "save to crop it as a template."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.view = MJPEGView()
        self.view.setMinimumHeight(540)
        if connection_manager.connected:
            cfg = connection_manager.config
            self.view.set_source(
                StreamSource(
                    host=_stream_host(cfg.host),
                    port=cfg.camera_stream_port,
                    path=f"camera/{camera_id}",
                )
            )
        self.view.installEventFilter(self)
        root.addWidget(self.view, 1)

        self._overlay = _RectOverlay(self.view, lambda: self._current_rect)
        self._overlay.show()
        self._overlay.setGeometry(self.view.rect())

        # Form row: name + action buttons
        form = QHBoxLayout()
        form.setSpacing(8)
        form.addWidget(QLabel("Template name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. clinch_front")
        form.addWidget(self.name_edit, 1)
        freeze_btn = secondary_button("Freeze / Resume", "crop")
        freeze_btn.setCheckable(True)
        freeze_btn.toggled.connect(self._on_freeze_toggled)
        self._freeze_btn = freeze_btn
        form.addWidget(freeze_btn)
        clear_btn = secondary_button("Clear selection", "refresh")
        clear_btn.clicked.connect(self._on_clear)
        form.addWidget(clear_btn)
        root.addLayout(form)

        self.perspective_toggle_btn = secondary_button(
            "  Perspective Runtime: ON",
            "mode",
        )
        self.perspective_toggle_btn.setCheckable(True)
        self.perspective_toggle_btn.setChecked(True)
        self.perspective_toggle_btn.setEnabled(connection_manager.connected)
        self.perspective_toggle_btn.clicked.connect(self._toggle_perspective_runtime)
        root.addWidget(self.perspective_toggle_btn)

        self.selection_label = QLabel("No selection")
        self.selection_label.setObjectName("Muted")
        root.addWidget(self.selection_label)

        btns = QDialogButtonBox()
        save_btn = QPushButton("  Save template")
        save_btn.setProperty("accent", True)
        save_btn.setIcon(icon("save", color="#FFFFFF"))
        save_btn.setIconSize(QSize(16, 16))
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("  Close")
        cancel_btn.setIcon(icon("close"))
        cancel_btn.setIconSize(QSize(16, 16))
        cancel_btn.clicked.connect(self.reject)
        btns.addButton(save_btn, QDialogButtonBox.AcceptRole)
        btns.addButton(cancel_btn, QDialogButtonBox.RejectRole)
        root.addWidget(btns)

        self._saved_path: Optional[Path] = None

    # ---------------------------------------------------------------- events
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.view:
            if isinstance(event, QMouseEvent) and event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self._origin = event.pos()
                    self._current_rect = QRect(self._origin, QSize())
                    self._overlay.update()
            elif isinstance(event, QMouseEvent) and event.type() == QEvent.MouseMove:
                if self._origin is not None:
                    self._current_rect = QRect(self._origin, event.pos()).normalized()
                    self._update_selection_label()
                    self._overlay.update()
            elif isinstance(event, QMouseEvent) and event.type() == QEvent.MouseButtonRelease:
                if event.button() == Qt.LeftButton and self._origin is not None:
                    self._current_rect = QRect(self._origin, event.pos()).normalized()
                    self._origin = None
                    self._update_selection_label()
                    self._overlay.update()
            elif event.type() == QEvent.Resize:
                self._overlay.setGeometry(self.view.rect())
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.view.rect())

    # ---------------------------------------------------------------- actions
    def _on_freeze_toggled(self, frozen: bool) -> None:
        if frozen:
            self.view.stop()
            self._freeze_btn.setText("Resume live")
        else:
            self.view.start()
            self._freeze_btn.setText("Freeze / Resume")

    def _on_clear(self) -> None:
        self._current_rect = QRect()
        self._origin = None
        self._update_selection_label()
        self._overlay.update()

    def _toggle_perspective_runtime(self, checked: bool) -> None:
        if not connection_manager.connected:
            QMessageBox.information(
                self,
                "Not connected",
                "Connect to a cnode device first.",
            )
            self.perspective_toggle_btn.blockSignals(True)
            self.perspective_toggle_btn.setChecked(not checked)
            self.perspective_toggle_btn.blockSignals(False)
            return
        ok = commander.send(
            "calibration.set_camera_perspective",
            [self._camera_id, bool(checked)],
        )
        self.perspective_toggle_btn.setText(
            "  Perspective Runtime: ON" if checked else "  Perspective Runtime: OFF"
        )
        if not ok:
            QMessageBox.warning(
                self,
                "Command failed",
                "Could not send calibration.set_camera_perspective command.",
            )

    def _update_selection_label(self) -> None:
        if self._current_rect.isEmpty():
            self.selection_label.setText("No selection")
        else:
            r = self._current_rect
            self.selection_label.setText(
                f"Selection: ({r.x()}, {r.y()})  {r.width()}×{r.height()} px (view coords)"
            )

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Enter a template name first.")
            return
        if self._current_rect.isEmpty() or self._current_rect.width() < 4:
            QMessageBox.warning(self, "No selection", "Drag a rectangle on the live view first.")
            return
        image = self.view.current_image()
        if image is None:
            QMessageBox.warning(
                self,
                "No frame",
                "No camera frame yet — the stream may be offline.",
            )
            return
        displayed = self.view.displayed_rect()
        if displayed is None or displayed.width() == 0 or displayed.height() == 0:
            QMessageBox.warning(self, "Invalid view", "Could not determine image bounds.")
            return

        # Translate selection from view-widget coords to image coords.
        sel_in_displayed = self._current_rect.translated(
            -displayed.x(), -displayed.y()
        ).intersected(QRect(0, 0, displayed.width(), displayed.height()))
        if sel_in_displayed.width() < 4 or sel_in_displayed.height() < 4:
            QMessageBox.warning(self, "Out of bounds", "Draw the selection over the camera image.")
            return

        sx = image.width() / displayed.width()
        sy = image.height() / displayed.height()
        img_rect = QRect(
            int(sel_in_displayed.x() * sx),
            int(sel_in_displayed.y() * sy),
            max(1, int(sel_in_displayed.width() * sx)),
            max(1, int(sel_in_displayed.height() * sy)),
        )
        cropped = image.copy(img_rect)
        if cropped.isNull():
            QMessageBox.warning(self, "Empty crop", "Cropped image is empty.")
            return

        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        if not safe.lower().endswith((".png", ".jpg", ".jpeg")):
            safe += ".png"
        target = TEMPLATES_DIR / safe
        if not cropped.save(str(target)):
            QMessageBox.warning(self, "Save failed", f"Could not write {target}.")
            return

        self._saved_path = target
        log.info("Saved template %s (%dx%d)", target, cropped.width(), cropped.height())
        QMessageBox.information(
            self,
            "Template saved",
            f"Saved <b>{target.name}</b> ({cropped.width()}×{cropped.height()}) to\n{TEMPLATES_DIR}",
        )

    def saved_path(self) -> Optional[Path]:
        return self._saved_path

    def done(self, result: int) -> None:  # noqa: D401
        # Stop the MJPEG worker before the dialog tears down — otherwise the
        # embedded QThread is destroyed while still running and the process
        # crashes.
        try:
            self.view.stop()
        except Exception:  # pragma: no cover
            pass
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: D401, N802
        try:
            self.view.stop()
        except Exception:  # pragma: no cover
            pass
        super().closeEvent(event)


class _RectOverlay(QWidget):
    def __init__(self, parent: QWidget, rect_getter) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._rect_getter = rect_getter

    def paintEvent(self, event) -> None:  # noqa: N802
        rect: QRect = self._rect_getter()
        if rect is None or rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor("#F59E0B"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QColor(245, 158, 11, 55))
        painter.drawRect(rect)


# ---------------------------------------------------------------------------
# Camera details panel
# ---------------------------------------------------------------------------

class _CameraDetails(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._camera_id: Optional[str] = None
        self._payload: Dict[str, Any] = {}
        self._local = False
        self._on_change = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QLabel("Select a camera")
        self.header.setStyleSheet("font-size: 14pt; font-weight: 700;")
        self.header.setContentsMargins(16, 16, 16, 16)
        root.addWidget(self.header)

        stream_card = Card(title="Live feed", icon_name="cameras")
        self.view = MJPEGView()
        self.view.setMinimumHeight(520)
        stream_card.add(self.view)
        hint = QLabel(
            "Template capture lives on the <b>Detection Profiles</b> page — pick a "
            "profile, choose this camera in the dropdown, and click "
            "<i>Capture template</i>."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        stream_card.body().addWidget(hint)
        root.addWidget(stream_card)

        options = Card(title="Transform options", icon_name="settings")
        form = QFormLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(
            "0 (default webcam)  ·  rtsp://…  ·  /path/to/video.mp4"
        )
        self.width_spin = QSpinBox()
        self.width_spin.setRange(160, 7680)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(120, 4320)
        self.mirror_h = QCheckBox("Mirror horizontally")
        self.mirror_v = QCheckBox("Mirror vertically")
        self.rotate_spin = QSpinBox()
        self.rotate_spin.setRange(-270, 270)
        self.rotate_spin.setSingleStep(90)

        form.addRow("Source", self.source_edit)
        form.addRow("Frame width", self.width_spin)
        form.addRow("Frame height", self.height_spin)
        form.addRow(self.mirror_h)
        form.addRow(self.mirror_v)
        form.addRow("Rotation (deg)", self.rotate_spin)
        options.body().addLayout(form)

        source_hint = QLabel(
            "Enter a device index (e.g. <b>0</b>, <b>1</b>) for a local camera, or "
            "paste a URL / file path (e.g. <b>rtsp://user:pass@host/stream</b>) "
            "for a network / file source."
        )
        source_hint.setObjectName("Muted")
        source_hint.setWordWrap(True)
        options.body().addWidget(source_hint)

        perspective_hint = QLabel(
            "Perspective transform is now configured per-workflow on the "
            "<b>Workflows</b> page — add this camera to a workflow and edit "
            "the perspective there."
        )
        perspective_hint.setObjectName("Muted")
        perspective_hint.setWordWrap(True)
        options.body().addWidget(perspective_hint)

        save_row = QHBoxLayout()
        self.save_btn = primary_button("  Save", "save")
        self.save_btn.clicked.connect(self._save)
        self.delete_btn = secondary_button("  Delete camera", "trash")
        self.delete_btn.clicked.connect(self._delete)
        save_row.addWidget(self.save_btn)
        save_row.addWidget(self.delete_btn)
        save_row.addStretch(1)
        options.body().addLayout(save_row)

        root.addWidget(options)
        root.addStretch(1)

    def set_camera(self, camera_id: Optional[str], data: Optional[Dict[str, Any]], local: bool = False, on_change=None) -> None:
        self._camera_id = camera_id
        self._local = local
        self._on_change = on_change
        data = dict(data or {})
        self._payload = dict(data)
        # Perspective now lives in the workflow, never on the camera config.
        self._payload.pop("perspective", None)
        if camera_id is None:
            self.header.setText("Select a camera")
            self.source_edit.setText("")
            self.view.stop()
            return
        self.header.setText(camera_id)
        src = data.get("source", 0)
        self.source_edit.setText("" if src is None else str(src))
        self.width_spin.setValue(int(data.get("frame_width", 1280)))
        self.height_spin.setValue(int(data.get("frame_height", 720)))
        self.mirror_h.setChecked(bool(data.get("mirror_h", False)))
        self.mirror_v.setChecked(bool(data.get("mirror_v", False)))
        self.rotate_spin.setValue(int(data.get("rotate_deg", 0)))

        if connection_manager.connected:
            cfg = connection_manager.config
            self.view.set_source(
                StreamSource(
                    host="127.0.0.1" if local else cfg.host,
                    port=cfg.camera_stream_port,
                    path=f"camera/{camera_id}",
                )
            )
        else:
            self.view.set_source(None)

    def _save(self) -> None:
        if self._camera_id is None:
            return
        if not self._local and not api_client.available:
            QMessageBox.information(
                self, "Not connected", "Connect to a cnode device first."
            )
            return
        self._payload.update({
            "source": _parse_source(self.source_edit.text()),
            "frame_width": self.width_spin.value(),
            "frame_height": self.height_spin.value(),
            "mirror_h": self.mirror_h.isChecked(),
            "mirror_v": self.mirror_v.isChecked(),
            "rotate_deg": self.rotate_spin.value(),
        })
        self._payload.pop("perspective", None)
        try:
            if self._local:
                local_camera_config.save(self._camera_id, self._payload)
                local_camera_server.configure_local_camera(self._camera_id)
            else:
                api_client.client.set_camera(self._camera_id, self._payload)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        QMessageBox.information(self, "Saved", f"Camera '{self._camera_id}' saved.")
        if self._on_change:
            self._on_change()

    def _delete(self) -> None:
        if self._camera_id is None:
            return
        if not self._local and not api_client.available:
            QMessageBox.information(
                self, "Not connected", "Connect to a cnode device first."
            )
            return
        if (
            QMessageBox.question(
                self,
                "Delete camera",
                f"Remove camera <b>{self._camera_id}</b>?",
            )
            != QMessageBox.Yes
        ):
            return
        try:
            if self._local:
                local_camera_config.delete(self._camera_id)
                local_camera_server.configure_local_camera()
            else:
                api_client.client.delete_camera(self._camera_id)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        removed = self._camera_id
        self.view.stop()
        self.set_camera(None, None)
        parent = self.parent()
        while parent is not None and not isinstance(parent, CamerasPage):
            parent = parent.parent()
        if isinstance(parent, CamerasPage):
            parent._load()  # refresh list
        log.info("Deleted camera %s", removed)


# ---------------------------------------------------------------------------
# Cameras page
# ---------------------------------------------------------------------------

class CamerasPage(PageBase):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        add_btn = primary_button("  Add Camera", "plus")
        add_btn.clicked.connect(self._on_add)
        header = PageHeader(
            "Cameras",
            right_widget=add_btn,
            icon_name="cameras",
            on_refresh=lambda: self._load(),
        )
        super().__init__(header, parent)

        self._cameras: Dict[str, Dict[str, Any]] = {}
        self._local_mode = False

        split = QHBoxLayout()
        split.setSpacing(0)
        self.content().addLayout(split)

        left = Card(title="Cameras", icon_name="cameras")
        left.setMinimumWidth(240)
        left.setMaximumWidth(280)
        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_select)
        left.add(self.list_widget)
        split.addWidget(left, 0)

        self.details = _CameraDetails()
        split.addWidget(self.details, 1)
        self.content().addStretch(1)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("Muted")
        self.empty_label.setWordWrap(True)
        left.add(self.empty_label)

        connection_manager.connected_changed.connect(lambda _c: self._load())
        api_client.availability_changed.connect(lambda _a: self._load())
        self._load()

    def _load(self) -> None:
        self._local_mode = bool(connection_manager.connected and connection_manager.config.host.strip().lower() in {"localhost", "127.0.0.1", "::1"} and not api_client.available)
        if self._local_mode:
            self._cameras = local_camera_config.list()
        elif not api_client.available:
            self._cameras = {}
            self._refresh_list()
            self.empty_label.setText(
                "Not connected — connect to a cnode device from the top bar."
            )
            self.empty_label.setVisible(True)
            self.details.set_camera(None, None)
            return
        else:
            try:
                cfg = api_client.client.get_handler_config()
                self._cameras = dict(cfg.get("cameras") or {})
            except CNodeManagerError as exc:
                log.info("list_cameras failed: %s", exc)
                self._cameras = {}
        self._refresh_list()
        self.empty_label.setVisible(not self._cameras)
        if not self._cameras:
            self.empty_label.setText(
                "No cameras configured yet. Click <b>Add Camera</b> to create one."
            )
        if self._cameras:
            self.list_widget.setCurrentRow(0)
        else:
            self.details.set_camera(None, None)

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for name in self._cameras:
            self.list_widget.addItem(QListWidgetItem(name))

    def _on_select(self) -> None:
        items = self.list_widget.selectedItems()
        if not items:
            self.details.set_camera(None, None)
            return
        name = items[0].text()
        if self._local_mode:
            local_camera_server.configure_local_camera(name)
        self.details.set_camera(name, self._cameras.get(name), local=self._local_mode, on_change=self._load)

    def _on_add(self) -> None:
        local_mode = bool(connection_manager.connected and connection_manager.config.host.strip().lower() in {"localhost", "127.0.0.1", "::1"} and not api_client.available)
        if not api_client.available and not local_mode:
            log.warning(
                "Add camera: api_client unavailable (connected=%s, active=%s)",
                connection_manager.connected,
                connection_manager.active_device.host if connection_manager.active_device else None,
            )
            QMessageBox.information(
                self, "Not connected", "Connect to a cnode device first."
            )
            return
        name, ok = QInputDialog.getText(self, "New camera", "Camera id:")
        name = name.strip()
        if not ok or not name:
            return
        if name in self._cameras:
            QMessageBox.warning(self, "Exists", "Camera already exists.")
            return
        source_text, ok = QInputDialog.getText(
            self,
            "Camera source",
            "Source (device index e.g. 0, or RTSP URL / file path):",
            text="0",
        )
        if not ok:
            return
        default_payload = {
            "source": _parse_source(source_text),
            "frame_width": 1280,
            "frame_height": 720,
            "mirror_h": False,
            "mirror_v": False,
            "rotate_deg": 0,
        }
        try:
            if local_mode:
                local_camera_config.save(name, default_payload)
                local_camera_server.configure_local_camera(name)
            else:
                api_client.client.set_camera(name, default_payload)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Create failed", str(exc))
            return
        self._load()
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).text() == name:
                self.list_widget.setCurrentRow(row)
                break

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.details.view.stop()
