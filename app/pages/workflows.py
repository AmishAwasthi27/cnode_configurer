"""Workflows configuration page — driven by cnode_manager_sdk."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFormLayout,
    QFrame,
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

from app.icons import icon, pixmap
from app.managers import api_client, connection_manager
from app.pages._common import PageBase, PageHeader, primary_button, secondary_button
from app.pages.cameras import _PerspectivePicker
from app.widgets.card import Card

log = logging.getLogger("WorkflowsPage")

try:
    from cnode_manager_sdk import CNodeManagerError, Workflow as SDKWorkflow
except ImportError:  # pragma: no cover
    CNodeManagerError = Exception  # type: ignore[assignment]
    SDKWorkflow = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Flow nodes
# ---------------------------------------------------------------------------

class _FlowNode(QFrame):
    def __init__(
        self,
        title: str,
        icon_name: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FlowNode")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        if icon_name is not None:
            icon_label = QLabel()
            icon_label.setPixmap(pixmap(icon_name, color="#2E3A59", size=18))
            icon_label.setFixedSize(20, 20)
            title_row.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 700;")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        layout.addLayout(title_row)
        self._body_layout = layout

    def add(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)


def _arrow() -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(pixmap("arrow-right", color="#2E3A59", size=26))
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedWidth(32)
    return lbl


# ---------------------------------------------------------------------------
# Details panel
# ---------------------------------------------------------------------------

_DEFAULT_DST = (640, 480)

# Runner classes cnode_handler currently ships with. Keep this in sync with
# ``cnode_handler/workflows/__init__.py`` (the ``WORKFLOWS`` dict).
WORKFLOW_TYPES: list[str] = ["simple_detect", "calibration"]


def _format_perspective(persp: Optional[Dict[str, Any]]) -> str:
    if not persp or not persp.get("src_pts"):
        return "No perspective set"
    pts = persp.get("src_pts") or []
    w = persp.get("dst_width")
    h = persp.get("dst_height")
    return f"{len(pts)} pts · {w}×{h}" if w and h else f"{len(pts)} pts"


class _PinButtonRow(QFrame):
    """Checkable pin buttons laid out in a row.

    * ``exclusive=True``  -> radio-like single-select (used for trigger input).
    * ``exclusive=False`` -> multi-select (used for relay output lists).
    """

    def __init__(
        self,
        *,
        exclusive: bool,
        include_null_option: bool = False,
        on_changed=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._exclusive = exclusive
        self._include_null_option = bool(include_null_option and exclusive)
        self._on_changed = on_changed
        self._pins: list[str] = []
        self._buttons: Dict[str, QPushButton] = {}
        self._null_key = "__NULL__"
        self.setStyleSheet(
            "QPushButton#PinButton {"
            "  padding: 4px 10px;"
            "  border: 1px solid #CBD5E1;"
            "  border-radius: 6px;"
            "  background: #F8FAFC;"
            "  color: #1F2937;"
            "  font-family: 'Consolas','Menlo',monospace;"
            "  font-weight: 600;"
            "}"
            "QPushButton#PinButton:hover { background: #EEF2FF; }"
            "QPushButton#PinButton:checked {"
            "  background: #2563EB;"
            "  color: white;"
            "  border-color: #1D4ED8;"
            "}"
            "QPushButton#PinButton:disabled {"
            "  color: #9CA3AF;"
            "  background: #F3F4F6;"
            "}"
        )

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._group = QButtonGroup(self)
        # Keep the group non-exclusive so an explicit NULL selection can
        # coexist with "no selection", while we still enforce single-select
        # behavior in `_on_button_toggled`.
        self._group.setExclusive(False)

        self._empty_label = QLabel("(no pins available)")
        self._empty_label.setObjectName("Muted")
        self._layout.addWidget(self._empty_label)
        self._layout.addStretch(1)

    def set_pins(self, pins: list[str]) -> None:
        self._pins = list(pins)
        for btn in list(self._buttons.values()):
            self._group.removeButton(btn)
            btn.setParent(None)
            btn.deleteLater()
        self._buttons.clear()
        while self._layout.count() > 0:
            self._layout.takeAt(0)
        if self._include_null_option:
            null_btn = QPushButton("None")
            null_btn.setObjectName("PinButton")
            null_btn.setCheckable(True)
            null_btn.setMinimumWidth(56)
            null_btn.setCursor(Qt.PointingHandCursor)
            null_btn.toggled.connect(
                lambda checked, key=self._null_key: self._on_button_toggled(key, checked)
            )
            self._group.addButton(null_btn)
            self._buttons[self._null_key] = null_btn
            self._layout.addWidget(null_btn)
        for pin in pins:
            btn = QPushButton(pin)
            btn.setObjectName("PinButton")
            btn.setCheckable(True)
            btn.setMinimumWidth(56)
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda checked, key=pin: self._on_button_toggled(key, checked))
            self._group.addButton(btn)
            self._buttons[pin] = btn
            self._layout.addWidget(btn)
        if not pins and not self._include_null_option:
            self._layout.addWidget(self._empty_label)
        self._layout.addStretch(1)

    def _on_button_toggled(self, key: str, checked: bool) -> None:
        if checked and self._exclusive:
            for other_key, btn in self._buttons.items():
                if other_key == key:
                    continue
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._emit_changed(checked)

    def _emit_changed(self, _checked: bool) -> None:
        if self._on_changed is not None:
            self._on_changed(self.value())

    def set_value(self, value) -> None:
        """Apply ``value`` (str for exclusive, list[str] otherwise)."""
        selected = set()
        if self._exclusive:
            if value is None and self._include_null_option:
                selected.add(self._null_key)
            elif value:
                selected.add(value)
        else:
            selected = set(value or [])
        for pin, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(pin in selected)
            btn.blockSignals(False)

    def value(self):
        checked = [p for p, b in self._buttons.items() if b.isChecked()]
        if self._exclusive:
            if not checked:
                return None if self._include_null_option else ""
            if checked[0] == self._null_key:
                return None
            return checked[0]
        return checked

    def set_editable(self, enabled: bool) -> None:
        for btn in self._buttons.values():
            btn.setEnabled(enabled)


class _CameraConfigRow(QFrame):
    """One row in the workflow's Cameras card.

    Shows the camera id, a detection-profile selector, a perspective summary,
    and edit / reset / remove buttons.
    """

    def __init__(
        self,
        camera_id: str,
        config: Dict[str, Any],
        profile_names: list[str],
        on_edit_perspective,
        on_reset_perspective,
        on_remove,
        on_profile_changed,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WorkflowCameraRow")
        self._camera_id = camera_id

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)

        cam_label = QLabel()
        cam_icon = QLabel()
        cam_icon.setPixmap(pixmap("cameras", color="#2E3A59", size=16))
        row.addWidget(cam_icon)
        cam_label.setText(f"<b>{camera_id}</b>")
        cam_label.setMinimumWidth(80)
        row.addWidget(cam_label)

        row.addWidget(QLabel("Detection profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self.profile_combo.addItem("(none)", "")
        for name in profile_names:
            self.profile_combo.addItem(icon("profiles"), name, name)
        current = config.get("detection_profile") or ""
        idx = self.profile_combo.findData(current)
        if idx < 0 and current:
            self.profile_combo.addItem(icon("profiles"), current, current)
            idx = self.profile_combo.findData(current)
        self.profile_combo.setCurrentIndex(max(0, idx))
        self.profile_combo.currentIndexChanged.connect(
            lambda _i: on_profile_changed(camera_id, self.profile_combo.currentData() or "")
        )
        row.addWidget(self.profile_combo)

        edit_btn = secondary_button("  Perspective\u2026", "edit")
        edit_btn.clicked.connect(lambda: on_edit_perspective(camera_id))
        row.addWidget(edit_btn)

        reset_btn = secondary_button("", "refresh")
        reset_btn.setToolTip("Clear this camera's perspective transform")
        reset_btn.clicked.connect(lambda: on_reset_perspective(camera_id))
        row.addWidget(reset_btn)

        row.addSpacing(8)
        self.persp_label = QLabel(_format_perspective(config.get("perspective")))
        self.persp_label.setObjectName("Muted")
        row.addWidget(self.persp_label, 1)

        remove_btn = secondary_button("  Remove", "trash")
        remove_btn.setToolTip(f"Remove {camera_id} from this workflow")
        remove_btn.setObjectName("WorkflowCameraRemove")
        remove_btn.setStyleSheet(
            "QPushButton#WorkflowCameraRemove {"
            "  color: #B91C1C;"
            "  border: 1px solid #FCA5A5;"
            "  background: #FEF2F2;"
            "  padding: 4px 10px;"
            "  border-radius: 6px;"
            "  font-weight: 600;"
            "}"
            "QPushButton#WorkflowCameraRemove:hover {"
            "  background: #FEE2E2;"
            "}"
        )
        remove_btn.clicked.connect(lambda: on_remove(camera_id))
        row.addWidget(remove_btn)

    def set_perspective_text(self, persp: Optional[Dict[str, Any]]) -> None:
        self.persp_label.setText(_format_perspective(persp))


class _WorkflowDetails(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._workflow: Optional["SDKWorkflow"] = None
        self._cameras_cfg: Dict[str, Dict[str, Any]] = {}
        self._available_cameras: list[str] = []
        self._profile_names: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QLabel("Select a workflow")
        self.header.setStyleSheet("font-size: 14pt; font-weight: 700;")
        self.header.setContentsMargins(16, 16, 16, 16)
        root.addWidget(self.header)

        # Flow chart
        flow_card = Card(title="Control flow", icon_name="workflows")
        flow_row = QHBoxLayout()
        flow_row.setSpacing(0)
        flow_row.setContentsMargins(0, 0, 0, 0)

        self.trigger_node = _FlowNode("1. Trigger", "trigger")
        self.trigger_label = QLabel("—")
        self.trigger_node.add(self.trigger_label)

        self.detection_node = _FlowNode("2. Detection", "detect")
        self.detection_label = QLabel("—")
        self.detection_node.add(self.detection_label)

        self.profiles_node = _FlowNode("2.1 Detection profiles", "profiles")
        self.profiles_list = QLabel("—")
        self.profiles_list.setWordWrap(True)
        self.profiles_node.add(self.profiles_list)

        self.outputs_node = _FlowNode("3. Relay outputs", "relay")
        self.outputs_label = QLabel("—")
        self.outputs_node.add(self.outputs_label)

        flow_row.addWidget(self.trigger_node)
        flow_row.addWidget(_arrow())
        flow_row.addWidget(self.detection_node)
        flow_row.addWidget(_arrow())
        flow_row.addWidget(self.profiles_node)
        flow_row.addWidget(_arrow())
        flow_row.addWidget(self.outputs_node)
        flow_row.addStretch(1)
        flow_card.body().addLayout(flow_row)
        root.addWidget(flow_card)

        # Cameras card
        self.cameras_card = Card(title="Cameras", icon_name="cameras")
        self.cameras_empty_label = QLabel(
            "No cameras assigned to this workflow. Click <b>Add camera</b> to "
            "attach a camera from <code>cnode_handler.json</code>."
        )
        self.cameras_empty_label.setObjectName("Muted")
        self.cameras_empty_label.setWordWrap(True)
        self.cameras_card.body().addWidget(self.cameras_empty_label)

        self._camera_rows_container = QWidget()
        self._camera_rows_layout = QVBoxLayout(self._camera_rows_container)
        self._camera_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._camera_rows_layout.setSpacing(4)
        self.cameras_card.body().addWidget(self._camera_rows_container)

        cameras_action_row = QHBoxLayout()
        self.add_camera_btn = primary_button("  Add camera", "plus")
        self.add_camera_btn.clicked.connect(self._on_add_camera)
        cameras_action_row.addWidget(self.add_camera_btn)
        cameras_action_row.addStretch(1)
        self.cameras_card.body().addLayout(cameras_action_row)
        root.addWidget(self.cameras_card)

        # Edit form
        form_card = Card(title="Edit settings", icon_name="edit")
        form = QFormLayout()
        self.desc_edit = QLineEdit()
        # ``type`` selects which runner class cnode_handler will use for
        # this workflow. Only the two runner types cnode_handler ships
        # with are offered here.
        self.type_combo = QComboBox()
        self.type_combo.addItems(WORKFLOW_TYPES)
        self.trigger_row = _PinButtonRow(exclusive=True)
        # Separate reset pins for YES and NO branches.
        self.reset_yes_row = _PinButtonRow(exclusive=True, include_null_option=True)
        self.reset_no_row = _PinButtonRow(exclusive=True, include_null_option=True)
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 20)
        self.delay_on_detection_spin = QDoubleSpinBox()
        self.delay_on_detection_spin.setRange(0.0, 9999.0)
        self.delay_on_detection_spin.setDecimals(3)
        self.delay_on_detection_spin.setSingleStep(0.1)
        self.delay_on_detection_spin.setSuffix(" s")
        self.yes_when_combo = QComboBox()
        self.yes_when_combo.addItems(["", "present", "removed"])
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(["first", "majority", "all"])
        self.outputs_yes_row = _PinButtonRow(exclusive=False)
        self.outputs_no_row = _PinButtonRow(exclusive=False)

        form.addRow("Description", self.desc_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("Trigger input", self.trigger_row)
        form.addRow("Reset input (yes)", self.reset_yes_row)
        form.addRow("Reset input (no)", self.reset_no_row)
        form.addRow("Detection repeat", self.repeat_spin)
        form.addRow("Delay on detection", self.delay_on_detection_spin)
        form.addRow("Yes when", self.yes_when_combo)
        form.addRow("Best-of strategy", self.strategy_combo)
        form.addRow("Outputs (yes)", self.outputs_yes_row)
        form.addRow("Outputs (no)", self.outputs_no_row)
        form_card.body().addLayout(form)

        button_row = QHBoxLayout()
        self.save_btn = primary_button("  Save", "save")
        self.save_btn.clicked.connect(self._save)
        self.delete_btn = secondary_button("  Delete", "trash")
        self.delete_btn.clicked.connect(self._delete)
        button_row.addWidget(self.save_btn)
        button_row.addWidget(self.delete_btn)
        button_row.addStretch(1)
        form_card.body().addLayout(button_row)

        root.addWidget(form_card)
        root.addStretch(1)

        self._set_editable(False)

    # --------------------------------------------------------- public API
    def workflow(self) -> Optional["SDKWorkflow"]:
        return self._workflow

    def set_available_cameras(self, camera_ids: list[str]) -> None:
        """Inform the panel which cameras the device exposes (from handler config)."""
        self._available_cameras = list(camera_ids)

    def set_available_pins(self, inputs: list[str], outputs: list[str]) -> None:
        """Re-render the trigger/output button rows with device-configured pins."""
        current_trigger = self.trigger_row.value()
        current_reset_yes = self.reset_yes_row.value()
        current_reset_no = self.reset_no_row.value()
        current_yes = self.outputs_yes_row.value()
        current_no = self.outputs_no_row.value()
        self.trigger_row.set_pins(inputs)
        self.reset_yes_row.set_pins(inputs)
        self.reset_no_row.set_pins(inputs)
        self.outputs_yes_row.set_pins(outputs)
        self.outputs_no_row.set_pins(outputs)
        self.trigger_row.set_value(current_trigger)
        self.reset_yes_row.set_value(current_reset_yes)
        self.reset_no_row.set_value(current_reset_no)
        self.outputs_yes_row.set_value(current_yes)
        self.outputs_no_row.set_value(current_no)

    def set_workflow(self, workflow: Optional["SDKWorkflow"]) -> None:
        self._workflow = workflow
        if workflow is None:
            self.header.setText("Select a workflow")
            self.trigger_label.setText("—")
            self.detection_label.setText("—")
            self.profiles_list.setText("—")
            self.outputs_label.setText("—")
            self.desc_edit.setText("")
            self.type_combo.setCurrentIndex(0)
            self.trigger_row.set_value("")
            self.reset_yes_row.set_value("")
            self.reset_no_row.set_value("")
            self.outputs_yes_row.set_value([])
            self.outputs_no_row.set_value([])
            self.repeat_spin.setValue(1)
            self.delay_on_detection_spin.setValue(0.0)
            self.yes_when_combo.setCurrentIndex(0)
            self.strategy_combo.setCurrentIndex(0)
            self._cameras_cfg = {}
            self._profile_names = []
            self._rebuild_camera_rows()
            self._set_editable(False)
            return

        composed = workflow.compose or workflow.data or {}
        wf_type = composed.get("type") or ""
        self.header.setText(
            f"{workflow.name}  ·  type: {wf_type}" if wf_type else workflow.name
        )
        trigger_in = composed.get("trigger_input") or "(none)"
        reset_yes = (
            composed.get("reset_input_yes")
            or composed.get("reset_input")
            or "(none)"
        )
        reset_no = (
            composed.get("reset_input_no")
            or composed.get("reset_input")
            or "(none)"
        )
        self.trigger_label.setText(
            f"Trigger: {trigger_in}\nReset yes: {reset_yes}\nReset no: {reset_no}"
        )
        self.detection_label.setText(
            f"Repeat {composed.get('detection_repeat_count', 1)}x · "
            f"Delay: {float(composed.get('delay_on_detection', 0.0)):.3f}s · "
            f"Yes when: {composed.get('yes_when') or '-'}"
        )
        profiles = composed.get("detection_profiles") or {}
        self._profile_names = list(profiles.keys())
        if profiles:
            self.profiles_list.setText(
                "\n".join(
                    f"• {n}  ({p.get('algorithm', '?')})"
                    for n, p in profiles.items()
                )
            )
        else:
            self.profiles_list.setText("(no profiles)")

        self.outputs_label.setText(
            f"YES: {', '.join(composed.get('outputs_yes') or []) or '-'}\n"
            f"NO:  {', '.join(composed.get('outputs_no') or []) or '-'}"
        )

        self.desc_edit.setText(workflow.description or composed.get("description", ""))
        type_idx = self.type_combo.findText(wf_type) if wf_type else -1
        # Fall back to the first known runner type when the workflow carries
        # an unknown ``type`` (older JSON or hand-edited entries) so the
        # strict dropdown always has a valid selection.
        self.type_combo.setCurrentIndex(type_idx if type_idx >= 0 else 0)
        self.trigger_row.set_value(composed.get("trigger_input", ""))
        reset_fallback = composed.get("reset_input", "")
        self.reset_yes_row.set_value(composed.get("reset_input_yes", reset_fallback))
        self.reset_no_row.set_value(composed.get("reset_input_no", reset_fallback))
        self.repeat_spin.setValue(int(composed.get("detection_repeat_count", 1)))
        try:
            delay_seconds = float(composed.get("delay_on_detection", 0.0))
        except (TypeError, ValueError):
            delay_seconds = 0.0
        self.delay_on_detection_spin.setValue(delay_seconds)
        yes_idx = self.yes_when_combo.findText(composed.get("yes_when", ""))
        self.yes_when_combo.setCurrentIndex(max(0, yes_idx))
        strat_idx = self.strategy_combo.findText(
            composed.get("best_of_strategy", "first")
        )
        self.strategy_combo.setCurrentIndex(max(0, strat_idx))
        self.outputs_yes_row.set_value(composed.get("outputs_yes") or [])
        self.outputs_no_row.set_value(composed.get("outputs_no") or [])

        # Seed the cameras working copy from the composed workflow.
        cameras_raw = composed.get("cameras") or {}
        self._cameras_cfg = {
            cam_id: dict(cfg or {}) for cam_id, cfg in cameras_raw.items()
        }
        self._rebuild_camera_rows()
        self._set_editable(True)

    # ------------------------------------------------------- camera rows
    def _rebuild_camera_rows(self) -> None:
        while self._camera_rows_layout.count():
            item = self._camera_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._cameras_cfg:
            self.cameras_empty_label.setVisible(True)
            return
        self.cameras_empty_label.setVisible(False)
        for cam_id in sorted(self._cameras_cfg):
            cfg = self._cameras_cfg[cam_id]
            row = _CameraConfigRow(
                camera_id=cam_id,
                config=cfg,
                profile_names=self._profile_names,
                on_edit_perspective=self._on_edit_perspective,
                on_reset_perspective=self._on_reset_perspective,
                on_remove=self._on_remove_camera,
                on_profile_changed=self._on_profile_changed,
                parent=self._camera_rows_container,
            )
            self._camera_rows_layout.addWidget(row)

    # ----------------------------------------------------- camera actions
    def _on_profile_changed(self, camera_id: str, profile_name: str) -> None:
        cfg = self._cameras_cfg.setdefault(camera_id, {})
        if profile_name:
            cfg["detection_profile"] = profile_name
        else:
            cfg.pop("detection_profile", None)

    def _on_edit_perspective(self, camera_id: str) -> None:
        if not connection_manager.connected:
            QMessageBox.information(
                self,
                "Not connected",
                "Connect to a cnode device first so the live feed is available.",
            )
            return
        cfg = self._cameras_cfg.setdefault(camera_id, {})
        persp = cfg.get("perspective") or {}
        existing_pts = persp.get("src_pts") or None
        dst_w = int(persp.get("dst_width") or _DEFAULT_DST[0])
        dst_h = int(persp.get("dst_height") or _DEFAULT_DST[1])
        dialog = _PerspectivePicker(
            camera_id,
            initial_points=[(int(p[0]), int(p[1])) for p in existing_pts]
            if existing_pts
            else None,
            initial_dst=(dst_w, dst_h),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        pts = dialog.points()
        dst_size = dialog.dst_size()
        cfg["perspective"] = {
            "src_pts": [[int(x), int(y)] for (x, y) in pts],
            "dst_width": dst_size[0],
            "dst_height": dst_size[1],
        }
        self._rebuild_camera_rows()

    def _on_reset_perspective(self, camera_id: str) -> None:
        cfg = self._cameras_cfg.get(camera_id)
        if cfg is None:
            return
        cfg.pop("perspective", None)
        self._rebuild_camera_rows()

    def _on_remove_camera(self, camera_id: str) -> None:
        if (
            QMessageBox.question(
                self,
                "Remove camera",
                f"Remove <b>{camera_id}</b> from this workflow?",
            )
            != QMessageBox.Yes
        ):
            return
        self._cameras_cfg.pop(camera_id, None)
        self._rebuild_camera_rows()

    def _on_add_camera(self) -> None:
        if self._workflow is None:
            return
        if not self._available_cameras:
            QMessageBox.information(
                self,
                "No cameras",
                "No cameras are configured in <code>cnode_handler.json</code>. "
                "Add a camera on the <b>Cameras</b> page first.",
            )
            return
        remaining = [
            cam_id
            for cam_id in self._available_cameras
            if cam_id not in self._cameras_cfg
        ]
        if not remaining:
            QMessageBox.information(
                self,
                "All cameras added",
                "Every camera available on the device is already attached to "
                "this workflow.",
            )
            return
        chosen, ok = QInputDialog.getItem(
            self,
            "Add camera",
            "Pick a camera to attach to this workflow:",
            remaining,
            0,
            False,
        )
        if not ok or not chosen:
            return
        default_profile = self._profile_names[0] if self._profile_names else ""
        self._cameras_cfg[chosen] = (
            {"detection_profile": default_profile} if default_profile else {}
        )
        self._rebuild_camera_rows()

    def _set_editable(self, enabled: bool) -> None:
        for w in (
            self.desc_edit,
            self.type_combo,
            self.repeat_spin,
            self.delay_on_detection_spin,
            self.yes_when_combo,
            self.strategy_combo,
        ):
            w.setEnabled(enabled)
        self.trigger_row.set_editable(enabled)
        self.reset_yes_row.set_editable(enabled)
        self.reset_no_row.set_editable(enabled)
        self.outputs_yes_row.set_editable(enabled)
        self.outputs_no_row.set_editable(enabled)
        self.save_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)
        self.add_camera_btn.setEnabled(enabled)
        self._camera_rows_container.setEnabled(enabled)

    # ------------------------------------------------------------ actions
    def _save(self) -> None:
        if self._workflow is None or not api_client.available:
            QMessageBox.information(
                self,
                "Not connected",
                "Connect to a cnode device to save workflows.",
            )
            return
        composed = dict(self._workflow.compose or self._workflow.data or {})
        composed.update(
            {
                "description": self.desc_edit.text(),
                "type": self.type_combo.currentText().strip(),
                "trigger_input": self.trigger_row.value(),
                "reset_input_yes": self.reset_yes_row.value(),
                "reset_input_no": self.reset_no_row.value(),
                "detection_repeat_count": self.repeat_spin.value(),
                "delay_on_detection": float(self.delay_on_detection_spin.value()),
                "yes_when": self.yes_when_combo.currentText(),
                "best_of_strategy": self.strategy_combo.currentText(),
                "outputs_yes": list(self.outputs_yes_row.value()),
                "outputs_no": list(self.outputs_no_row.value()),
                "cameras": {
                    cam_id: dict(cfg)
                    for cam_id, cfg in self._cameras_cfg.items()
                },
            }
        )
        # The old schema allowed a top-level perspective; it is now per-camera.
        composed.pop("perspective", None)
        try:
            updated = api_client.client.update_workflow(
                self._workflow.name,
                data=composed,
                description=self.desc_edit.text(),
            )
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._workflow = updated
        QMessageBox.information(self, "Saved", f"{updated.name} saved.")
        parent = self.parent()
        while parent is not None and not isinstance(parent, WorkflowsPage):
            parent = parent.parent()
        if isinstance(parent, WorkflowsPage):
            parent.reload()

    def _delete(self) -> None:
        if self._workflow is None or not api_client.available:
            return
        if (
            QMessageBox.question(
                self,
                "Delete",
                f"Delete workflow '{self._workflow.name}'?",
            )
            != QMessageBox.Yes
        ):
            return
        try:
            api_client.client.delete_workflow(self._workflow.name)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        parent = self.parent()
        while parent is not None and not isinstance(parent, WorkflowsPage):
            parent = parent.parent()
        if isinstance(parent, WorkflowsPage):
            parent.reload()


# ---------------------------------------------------------------------------
# Workflows page
# ---------------------------------------------------------------------------

class WorkflowsPage(PageBase):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        right_widget = QWidget()
        right = QHBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        self._import_btn = secondary_button("  Import from JSON", "download")
        self._import_btn.setToolTip(
            "Seed the cnode_manager DB from the existing "
            "DATA/configs/workflows.json file (useful after a fresh install)."
        )
        self._import_btn.clicked.connect(self._on_import)
        right.addWidget(self._import_btn)

        self._add_btn = primary_button("  Add Workflow", "plus")
        self._add_btn.clicked.connect(self._on_add)
        right.addWidget(self._add_btn)

        header = PageHeader(
            "Workflow: —",
            right_widget=right_widget,
            icon_name="workflows",
            on_refresh=lambda: self.reload(),
        )
        super().__init__(header, parent)
        self._header = header

        self._workflows: Dict[str, Any] = {}

        split = QHBoxLayout()
        split.setSpacing(0)
        self.content().addLayout(split)

        left = Card(title="Workflows", icon_name="workflows")
        left.setMinimumWidth(240)
        left.setMaximumWidth(280)
        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_select)
        left.add(self.list_widget)

        self.empty_list_label = QLabel("Not connected.")
        self.empty_list_label.setObjectName("Muted")
        self.empty_list_label.setWordWrap(True)
        left.add(self.empty_list_label)
        split.addWidget(left, 0)

        self.details = _WorkflowDetails()
        split.addWidget(self.details, 1)

        self.content().addStretch(1)

        api_client.availability_changed.connect(lambda _a: self.reload())
        connection_manager.connected_changed.connect(lambda _c: self.reload())
        self.reload()

    # ---------------------------------------------------------- data
    def reload(self) -> None:
        if not api_client.available:
            self._workflows = {}
            self._refresh_list()
            self.empty_list_label.setText(
                "Not connected to a cnode device — connect from the top bar to "
                "see workflows."
            )
            self.empty_list_label.setVisible(True)
            self.details.set_available_cameras([])
            self.details.set_workflow(None)
            self._header.set_title("Workflow: —")
            return

        # Camera + pin lists from handler config — used by the per-workflow
        # "Add camera" picker and the trigger / output button rows so we only
        # ever offer values that are actually configured on the device.
        camera_ids: list[str] = []
        input_pins: list[str] = []
        output_pins: list[str] = []
        try:
            cfg = api_client.client.get_handler_config()
            camera_ids = list((cfg.get("cameras") or {}).keys())
            gpio = cfg.get("gpio") or {}
            input_pins = list(gpio.get("input_pins") or [])
            output_pins = list(gpio.get("output_pins") or [])
        except CNodeManagerError as exc:
            log.info("get_handler_config failed: %s", exc)
        self.details.set_available_cameras(camera_ids)
        self.details.set_available_pins(input_pins, output_pins)

        try:
            raw = api_client.client.list_workflows()
        except CNodeManagerError as exc:
            log.info("list_workflows failed: %s", exc)
            raw = {}
        self._workflows = {}
        for name, data in (raw or {}).items():
            # ``calibration`` is reserved for internal device use; don't
            # expose it in the configurer's workflow list.
            if name == "calibration":
                continue
            try:
                wf = api_client.client.get_workflow(name)
                self._workflows[wf.name] = wf
            except CNodeManagerError as exc:
                log.info("get_workflow(%s) failed: %s", name, exc)
        self.empty_list_label.setVisible(not self._workflows)
        if not self._workflows:
            self.empty_list_label.setText(
                "No workflows in the cnode_manager DB yet.<br><br>"
                "Click <b>Import from JSON</b> to seed the DB from the "
                "existing <code>DATA/configs/workflows.json</code> file, or "
                "<b>Add Workflow</b> to create a new one."
            )
        self._refresh_list()
        if self._workflows:
            self.list_widget.setCurrentRow(0)
        else:
            self.details.set_workflow(None)
            self._header.set_title("Workflow: —")

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for name in self._workflows:
            self.list_widget.addItem(QListWidgetItem(name))

    def _on_select(self) -> None:
        items = self.list_widget.selectedItems()
        if not items:
            self.details.set_workflow(None)
            self._header.set_title("Workflow: —")
            return
        name = items[0].text()
        wf = self._workflows.get(name)
        self._header.set_title(f"Workflow: {name}")
        self.details.set_workflow(wf)

    def _on_import(self) -> None:
        if not api_client.available:
            QMessageBox.information(
                self, "Not connected", "Connect to a cnode device first."
            )
            return
        try:
            count = api_client.client.import_workflows()
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        log.info("Import from JSON: %d workflow(s)", count)
        QMessageBox.information(
            self,
            "Imported",
            f"Imported {count} workflow(s) from workflows.json into the DB.",
        )
        self.reload()

    def _on_add(self) -> None:
        if not api_client.available:
            log.warning(
                "Add workflow: api_client unavailable (connected=%s, active=%s, client=%s)",
                connection_manager.connected,
                connection_manager.active_device.host if connection_manager.active_device else None,
                api_client.client,
            )
            QMessageBox.information(
                self, "Not connected", "Connect to a cnode device first."
            )
            return
        name, ok = QInputDialog.getText(self, "New workflow", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        if name in self._workflows:
            QMessageBox.warning(self, "Exists", "A workflow with this name already exists.")
            return
        try:
            api_client.client.create_workflow(
                name=name,
                data={
                    "description": "",
                    "trigger_input": "",
                    "reset_input_yes": "",
                    "reset_input_no": "",
                    "detection_repeat_count": 1,
                    "delay_on_detection": 0.0,
                    "yes_when": "",
                    "outputs_yes": [],
                    "outputs_no": [],
                    "best_of_strategy": "first",
                    "detection_profiles": {},
                },
            )
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Create failed", str(exc))
            return
        self.reload()
