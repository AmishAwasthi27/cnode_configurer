"""Updates page: device firmware + app software."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QWidget,
)

from app.managers import api_client, notification_manager
from app.pages._common import PageBase, PageHeader, primary_button, secondary_button
from app.widgets.card import Card


class _UpdateRow(Card):
    def __init__(self, title: str, description: str, target: str, icon_name: str) -> None:
        super().__init__(title=title, icon_name=icon_name)
        self._target = target

        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setObjectName("Muted")
        self.body().addWidget(desc)

        meta_row = QHBoxLayout()
        self.current_label = QLabel("Current: unknown")
        self.latest_label = QLabel("Latest: unknown")
        self.status_label = QLabel("Status: idle")
        self.status_label.setAlignment(Qt.AlignRight)
        meta_row.addWidget(self.current_label)
        meta_row.addWidget(self.latest_label)
        meta_row.addStretch(1)
        meta_row.addWidget(self.status_label)
        self.body().addLayout(meta_row)

        btn_row = QHBoxLayout()
        self.check_btn = secondary_button("  Check", "refresh")
        self.check_btn.clicked.connect(self.check)
        self.install_btn = primary_button("  Install update", "download")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self.install)
        btn_row.addWidget(self.check_btn)
        btn_row.addWidget(self.install_btn)
        btn_row.addStretch(1)
        self.body().addLayout(btn_row)

    def check(self) -> None:
        info = api_client.check_updates() or {}
        data = info.get(self._target, {}) if isinstance(info, dict) else {}
        current = data.get("current") or "(backend unavailable)"
        latest = data.get("latest") or "(backend unavailable)"
        self.current_label.setText(f"Current: {current}")
        self.latest_label.setText(f"Latest: {latest}")
        if data.get("update_available"):
            self.install_btn.setEnabled(True)
            self.status_label.setText("Status: update available")
        else:
            self.install_btn.setEnabled(False)
            self.status_label.setText("Status: up to date" if data else "Status: unknown")

    def install(self) -> None:
        if QMessageBox.question(
            self,
            "Install update",
            f"Install update for {self._target}?",
        ) != QMessageBox.Yes:
            return
        result = api_client.trigger_update(self._target)
        if result is None:
            self.status_label.setText("Status: failed (backend offline)")
            notification_manager.add(
                "Update failed",
                f"Could not trigger update for {self._target}.",
                level="error",
            )
        else:
            self.status_label.setText("Status: installing…")
            notification_manager.add(
                "Update started",
                f"DAKPAL is installing update for {self._target}.",
                level="info",
            )


class UpdatesPage(PageBase):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        check_btn = primary_button("  Check for Updates", "refresh")
        check_btn.clicked.connect(self._check_all)
        header = PageHeader(
            "Updates",
            right_widget=check_btn,
            icon_name="updates",
            on_refresh=lambda: self._check_all(),
        )
        super().__init__(header, parent)

        self.device_row = _UpdateRow(
            "Device update",
            "Firmware and services running on the cnode device (managed via DAKPAL).",
            "device",
            "cameras",
        )
        self.software_row = _UpdateRow(
            "Software update",
            "This configurer application on your workstation (managed via DAKPAL).",
            "software",
            "logo",
        )

        self.content().addWidget(self.device_row)
        self.content().addWidget(self.software_row)

        self.content().addStretch(1)

    def _check_all(self) -> None:
        self.device_row.check()
        self.software_row.check()
