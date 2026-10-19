"""Test I/O by overriding handler inputs / relays via RabbitMQ commands.

All buttons on this page publish a JSON command on exchange
``moderindustry.handler.commands``; cnode_handler picks it up and applies the
requested override. The live state visible in the status strip comes from the
``StatusManager`` RMQ consumer that subscribes to ``moderindustry.status``.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.icons import icon, pixmap
from app.managers import api_client, commander, connection_manager, status_manager
from app.pages._common import PageBase, PageHeader, primary_button, secondary_button
from app.widgets.card import Card

log = logging.getLogger("IOTestPage")

try:
    from cnode_manager_sdk import CNodeManagerError
except ImportError:  # pragma: no cover
    CNodeManagerError = Exception  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# One pin row (name + live state + HIGH/LOW buttons)
# ---------------------------------------------------------------------------

class _PinRow(QFrame):
    def __init__(self, name: str, on_set, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.name = name
        self._on_set = on_set

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        self._state_dot = QLabel("●")
        self._state_dot.setFixedWidth(14)
        layout.addWidget(self._state_dot)

        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: 600; font-family: 'Consolas','Menlo',monospace;")
        name_label.setMinimumWidth(72)
        layout.addWidget(name_label)

        self._value_label = QLabel("—")
        self._value_label.setMinimumWidth(52)
        self._value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._value_label)

        layout.addStretch(1)

        high_btn = QPushButton("HIGH")
        high_btn.setFixedWidth(72)
        high_btn.clicked.connect(lambda: self._on_set(self.name, True))
        low_btn = QPushButton("LOW")
        low_btn.setFixedWidth(72)
        low_btn.clicked.connect(lambda: self._on_set(self.name, False))
        layout.addWidget(high_btn)
        layout.addWidget(low_btn)

    def set_value(self, value: Optional[bool]) -> None:
        if value is None:
            self._state_dot.setStyleSheet("color: #9CA3AF;")
            self._value_label.setText("—")
            return
        if value:
            self._state_dot.setStyleSheet("color: #16A34A;")
            self._value_label.setText("HIGH")
            self._value_label.setStyleSheet(
                "background: #16A34A; color: white; border-radius: 10px; padding: 2px 8px; font-weight: 700;"
            )
        else:
            self._state_dot.setStyleSheet("color: #9CA3AF;")
            self._value_label.setText("LOW")
            self._value_label.setStyleSheet(
                "background: #9CA3AF; color: white; border-radius: 10px; padding: 2px 8px; font-weight: 700;"
            )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

class IOTestPage(PageBase):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        header = PageHeader(
            "Control",
            icon_name="relay",
            on_refresh=lambda: self.reload(),
        )
        super().__init__(header, parent)

        self._input_rows: Dict[str, _PinRow] = {}
        self._output_rows: Dict[str, _PinRow] = {}

        # Override toggle
        override_card = Card(title="Global override", icon_name="mode")
        override_hint = QLabel(
            "Turn override on to let this UI drive inputs/relays directly. "
            "While on, the handler ignores real GPIO reads and uses the values "
            "you set below."
        )
        override_hint.setObjectName("Muted")
        override_hint.setWordWrap(True)
        override_card.body().addWidget(override_hint)
        override_row = QHBoxLayout()
        self.override_check = QCheckBox("Override GPIO")
        self.override_check.toggled.connect(self._on_override_toggle)
        override_row.addWidget(self.override_check)
        override_row.addStretch(1)
        self._override_status = QLabel("—")
        self._override_status.setObjectName("Muted")
        override_row.addWidget(self._override_status)
        override_card.body().addLayout(override_row)
        self.content().addWidget(override_card)

        # Inputs + outputs cards (side by side)
        pins_row = QHBoxLayout()
        pins_row.setSpacing(0)

        self.inputs_card = Card(title="Inputs", icon_name="trigger")
        self.inputs_host = QWidget()
        self.inputs_host_layout = QVBoxLayout(self.inputs_host)
        self.inputs_host_layout.setContentsMargins(0, 0, 0, 0)
        self.inputs_host_layout.setSpacing(4)
        self.inputs_card.body().addWidget(self.inputs_host)
        pins_row.addWidget(self.inputs_card)

        self.outputs_card = Card(title="Outputs (Relays)", icon_name="relay")
        self.outputs_host = QWidget()
        self.outputs_host_layout = QVBoxLayout(self.outputs_host)
        self.outputs_host_layout.setContentsMargins(0, 0, 0, 0)
        self.outputs_host_layout.setSpacing(4)
        self.outputs_card.body().addWidget(self.outputs_host)
        pins_row.addWidget(self.outputs_card)

        pins_row_widget = QWidget()
        pins_row_widget.setLayout(pins_row)
        self.content().addWidget(pins_row_widget)

        # Workflow controls
        wf_card = Card(title="Workflow controls", icon_name="workflows")
        wf_row = QHBoxLayout()
        self.workflow_combo = QComboBox()
        self.workflow_combo.setMinimumWidth(220)
        wf_row.addWidget(QLabel("Workflow:"))
        wf_row.addWidget(self.workflow_combo, 1)
        set_btn = secondary_button("  Set", "save")
        set_btn.clicked.connect(self._on_set_workflow)
        wf_row.addWidget(set_btn)
        run_btn = primary_button("  Run", "play")
        run_btn.setIcon(icon("save", color="#FFFFFF"))
        run_btn.clicked.connect(lambda: commander.run_workflow())
        stop_btn = secondary_button("  Stop", "close")
        stop_btn.clicked.connect(lambda: commander.stop_workflow())
        pause_btn = secondary_button("  Pause", "refresh")
        pause_btn.clicked.connect(lambda: commander.pause_workflow())
        wf_row.addWidget(run_btn)
        wf_row.addWidget(stop_btn)
        wf_row.addWidget(pause_btn)
        wf_card.body().addLayout(wf_row)
        self.content().addWidget(wf_card)

        # Calibration commands
        calib_card = Card(title="Calibration", icon_name="edit")
        calib_row = QHBoxLayout()
        read_btn = secondary_button("  Read config", "refresh")
        read_btn.clicked.connect(lambda: commander.calibration_read_wf_config())
        init_btn = secondary_button("  Initialize", "play")
        init_btn.setIcon(icon("refresh"))
        init_btn.clicked.connect(lambda: commander.calibration_initialize())
        set_profile_btn = secondary_button("  Set camera profile…", "edit")
        set_profile_btn.clicked.connect(self._on_set_camera_profile)
        calib_row.addWidget(read_btn)
        calib_row.addWidget(init_btn)
        calib_row.addWidget(set_profile_btn)
        calib_row.addStretch(1)
        calib_card.body().addLayout(calib_row)
        self.content().addWidget(calib_card)

        self.content().addStretch(1)

        # Signals
        status_manager.status_received.connect(self._on_status)
        connection_manager.connected_changed.connect(lambda _c: self.reload())
        api_client.availability_changed.connect(lambda _a: self.reload())
        self.reload()

    # ------------------------------------------------------------ data
    def reload(self) -> None:
        # Pin names come from the handler config.
        input_names: list[str] = []
        output_names: list[str] = []
        if api_client.available:
            try:
                cfg = api_client.client.get_handler_config()
                input_names = list((cfg.get("gpio") or {}).get("input_pins") or [])
                output_names = list((cfg.get("gpio") or {}).get("output_pins") or [])
            except CNodeManagerError as exc:
                log.info("handler config fetch failed: %s", exc)

        self._populate_pin_rows(
            self.inputs_host_layout,
            self._input_rows,
            input_names,
            on_set=self._on_set_input,
        )
        self._populate_pin_rows(
            self.outputs_host_layout,
            self._output_rows,
            output_names,
            on_set=self._on_set_output,
        )

        # Seed workflow combo
        workflows: list[str] = []
        if api_client.available:
            try:
                raw = api_client.client.list_workflows()
                workflows = list(raw.keys())
            except CNodeManagerError as exc:
                log.info("list_workflows failed: %s", exc)
        current = self.workflow_combo.currentText()
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for w in workflows:
            self.workflow_combo.addItem(icon("workflows"), w)
        if current and current in workflows:
            self.workflow_combo.setCurrentText(current)
        self.workflow_combo.blockSignals(False)

        # Show last known status if any
        last = getattr(status_manager, "last_status", None) or {}
        self._on_status(last)

    @staticmethod
    def _populate_pin_rows(layout, rows_dict, names, on_set) -> None:
        # Remove extras
        for name in list(rows_dict):
            if name not in names:
                widget = rows_dict.pop(name)
                layout.removeWidget(widget)
                widget.deleteLater()
        # Add new ones
        for name in names:
            if name in rows_dict:
                continue
            row = _PinRow(name, on_set)
            rows_dict[name] = row
            layout.addWidget(row)

    # --------------------------------------------------------- signals
    def _on_status(self, payload: dict) -> None:
        inputs = dict(payload.get("input_states") or {})
        outputs = dict(payload.get("output_states") or {})
        for name, row in self._input_rows.items():
            row.set_value(inputs.get(name))
        for name, row in self._output_rows.items():
            row.set_value(outputs.get(name))

    # --------------------------------------------------------- actions
    def _on_override_toggle(self, checked: bool) -> None:
        ok = commander.set_override(checked)
        if ok:
            self._override_status.setText(
                f"Override: {'ON' if checked else 'OFF'}"
            )
        else:
            self.override_check.blockSignals(True)
            self.override_check.setChecked(not checked)
            self.override_check.blockSignals(False)
            self._override_status.setText(
                "Override: (not connected)" if not connection_manager.connected
                else "Override: (send failed)"
            )

    def _on_set_input(self, name: str, value: bool) -> None:
        commander.set_input(name, value)

    def _on_set_output(self, name: str, value: bool) -> None:
        commander.set_output(name, value)

    def _on_set_workflow(self) -> None:
        name = self.workflow_combo.currentText()
        if not name:
            return
        commander.set_workflow(name)

    def _on_set_camera_profile(self) -> None:
        camera_id, ok1 = QInputDialog.getText(
            self, "Set camera profile", "Camera id:"
        )
        if not ok1 or not camera_id.strip():
            return
        profile_name, ok2 = QInputDialog.getText(
            self, "Set camera profile", "Profile name:"
        )
        if not ok2 or not profile_name.strip():
            return
        commander.calibration_set_camera_profile(
            camera_id.strip(), profile_name.strip()
        )


# Silence unused-import warnings in IDEs for helpers only referenced
# conditionally.
_ = pixmap
_ = QLineEdit
