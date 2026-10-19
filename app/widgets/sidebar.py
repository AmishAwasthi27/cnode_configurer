"""Collapsible sidebar navigation."""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.constants import MODE_MAINTENANCE
from app.icons import icon
from app.state import app_state


class Sidebar(QFrame):
    page_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(236)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 16, 10, 16)
        root.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: Dict[str, QPushButton] = {}
        self._maint_buttons: List[QPushButton] = []

        self._add_primary(root, "dashboard", "  Dashboard", "dashboard")
        self._add_primary(root, "live_detection", "  Live Detection", "detect")

        # Configure group (maintenance-only)
        self.configure_btn, self._configure_container = self._add_group(
            root,
            icon_name="configure",
            label="Configure",
            items=[
                ("workflows", "  Workflows", "workflows"),
                ("detection_profiles", "  Detection Profiles", "profiles"),
            ],
        )

        # Device group (maintenance-only)
        self.device_btn, self._device_container = self._add_group(
            root,
            icon_name="cameras",
            label="Device",
            items=[
                ("cameras", "  Cameras", "cameras"),
                ("io_test", "  Control", "relay"),
            ],
        )

        self._add_primary(root, "updates", "  Updates", "updates")

        root.addStretch(1)

        self._configure_expanded = False
        self._device_expanded = False
        self._apply_mode(app_state.mode)
        app_state.mode_changed.connect(self._apply_mode)
        app_state.sidebar_visibility_override_changed.connect(
            lambda _enabled: self._apply_mode(app_state.mode)
        )

        self._select("dashboard")

    def _add_primary(
        self,
        layout: QVBoxLayout,
        key: str,
        label: str,
        icon_name: str,
    ) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("SidebarButton")
        btn.setCheckable(True)
        btn.setIcon(icon(icon_name, color="#FFFFFF"))
        btn.setIconSize(QSize(20, 20))
        btn.clicked.connect(lambda _=False, k=key: self._select(k))
        self._group.addButton(btn)
        self._buttons[key] = btn
        layout.addWidget(btn)
        return btn

    def _add_group(
        self,
        layout: QVBoxLayout,
        *,
        icon_name: str,
        label: str,
        items: List[tuple[str, str, str]],
    ) -> tuple[QPushButton, QFrame]:
        """Create a maintenance-only collapsible group header + child container."""
        header = QPushButton(f"  {label}    \u25b8")
        header.setObjectName("SidebarButton")
        header.setCheckable(False)
        header.setIcon(icon(icon_name, color="#FFFFFF"))
        header.setIconSize(QSize(20, 20))
        header.clicked.connect(lambda _=False, h=header, lbl=label: self._toggle_group(h, lbl))
        layout.addWidget(header)

        container = QFrame()
        inner = QVBoxLayout(container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(2)
        for key, btn_label, sub_icon in items:
            btn = QPushButton(btn_label)
            btn.setObjectName("SidebarSubButton")
            btn.setCheckable(True)
            btn.setIcon(icon(sub_icon, color="#FFFFFF"))
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _=False, k=key: self._select(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            self._maint_buttons.append(btn)
            inner.addWidget(btn)
        layout.addWidget(container)
        # Bind container to the header so the toggle can flip its own arrow
        header.setProperty("_sidebar_container", container)
        return header, container

    def _toggle_group(self, header: QPushButton, label: str) -> None:
        container = header.property("_sidebar_container")
        expanded = not container.isVisible()
        container.setVisible(expanded)
        arrow = "\u25be" if expanded else "\u25b8"
        header.setText(f"  {label}    {arrow}")
        if header is self.configure_btn:
            self._configure_expanded = expanded
        elif header is self.device_btn:
            self._device_expanded = expanded

    def _select(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)
        self.page_selected.emit(key)

    def select_page(self, key: str) -> None:
        """Select a page from application code, keeping navigation state in sync."""
        self._select(key)

    def _apply_mode(self, mode: str) -> None:
        maintenance = mode == MODE_MAINTENANCE
        force_visible = app_state.sidebar_visibility_override
        visible = maintenance or force_visible
        self.configure_btn.setVisible(visible)
        self.device_btn.setVisible(visible)
        if not visible:
            self._configure_expanded = False
            self._device_expanded = False
            self._configure_container.setVisible(False)
            self._device_container.setVisible(False)
            self.configure_btn.setText("  Configure    \u25b8")
            self.device_btn.setText("  Device    \u25b8")
            for btn in self._maint_buttons:
                if btn.isChecked():
                    self._select("dashboard")
                    break
        else:
            self._configure_expanded = True
            self._device_expanded = True
            self._configure_container.setVisible(True)
            self._device_container.setVisible(True)
            self.configure_btn.setText("  Configure    \u25be")
            self.device_btn.setText("  Device    \u25be")
