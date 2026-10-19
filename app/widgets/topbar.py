"""Top bar widget: logo, connection, mode chip + toggle, notifications, account."""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.constants import MODE_MAINTENANCE, MODE_PRODUCTION
from app.icons import icon, pixmap
from app.managers import account_manager, api_client, connection_manager
from app.managers import notification_manager, status_manager
from app.state import app_state


def _normalize_handler_mode(raw: Optional[str]) -> Optional[str]:
    """Map device status ``mode`` string to MODE_PRODUCTION / MODE_MAINTENANCE."""
    if not raw:
        return None
    u = raw.strip().upper()
    if u in ("MAINTENANCE", "CALIBRATION"):
        return MODE_MAINTENANCE
    if u == "PRODUCTION":
        return MODE_PRODUCTION
    return None


class TopBar(QFrame):
    mode_toggle_requested = Signal()
    connection_clicked = Signal()
    notifications_clicked = Signal()
    account_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(60)

        self._device_name: Optional[str] = None
        self._last_handler_status: Dict[str, Any] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)

        # Logo
        logo_icon = QLabel()
        logo_icon.setPixmap(pixmap("logo", color="#FFFFFF", size=26))
        logo_icon.setFixedSize(28, 28)
        layout.addWidget(logo_icon)

        self.logo = QLabel("CNODE")
        self.logo.setObjectName("Logo")
        layout.addWidget(self.logo)

        # Connection button
        self.connection_btn = QPushButton("  Not connected")
        self.connection_btn.setIcon(icon("connection", color="#FFFFFF"))
        self.connection_btn.setIconSize(QSize(18, 18))
        self.connection_btn.clicked.connect(self.connection_clicked.emit)
        layout.addWidget(self.connection_btn)

        layout.addStretch(1)

        # Mode chip: local mode + device workflow / MAINTENANCE vs PRODUCTION from status
        self.mode_chip = QLabel()
        self.mode_chip.setObjectName("ModeChip")
        self.mode_chip.setAlignment(Qt.AlignCenter)
        self.mode_chip.setMinimumWidth(280)
        self.mode_chip.setMaximumWidth(520)
        self.mode_chip.setWordWrap(False)
        layout.addWidget(self.mode_chip, 0, Qt.AlignCenter)

        layout.addStretch(1)

        # Mode toggle
        self.mode_btn = QPushButton("Switch to Maintenance")
        self.mode_btn.setIcon(icon("mode", color="#FFFFFF"))
        self.mode_btn.setIconSize(QSize(18, 18))
        self.mode_btn.clicked.connect(self.mode_toggle_requested.emit)
        layout.addWidget(self.mode_btn)

        # Notifications
        self.notif_btn = QPushButton("  0")
        self.notif_btn.setIcon(icon("bell", color="#FFFFFF"))
        self.notif_btn.setIconSize(QSize(18, 18))
        self.notif_btn.clicked.connect(self.notifications_clicked.emit)
        layout.addWidget(self.notif_btn)

        # Account
        self.account_btn = QPushButton("  Sign In")
        self.account_btn.setIcon(icon("user", color="#FFFFFF"))
        self.account_btn.setIconSize(QSize(18, 18))
        self.account_btn.clicked.connect(self.account_clicked.emit)
        layout.addWidget(self.account_btn)

        for w in (self.connection_btn, self.mode_btn, self.notif_btn, self.account_btn):
            w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        app_state.mode_changed.connect(lambda _m: self._refresh_mode_chip())
        connection_manager.status_changed.connect(self._on_status_changed)
        connection_manager.connected_changed.connect(self._on_connected_changed)
        connection_manager.connection_changed.connect(
            self._on_connection_device_changed
        )
        notification_manager.changed.connect(self._on_notifications_changed)
        account_manager.changed.connect(self._on_account_changed)

        status_manager.status_received.connect(self._on_handler_status)
        api_client.availability_changed.connect(lambda _a: self._refresh_device_name())

        self._refresh_mode_chip()
        self._refresh_connection_label(connection_manager.connected)
        self._on_notifications_changed()
        self._on_account_changed()

    @property
    def device_display_name(self) -> str:
        """Human label for the device: ``device_name`` from handler config, else host."""
        if self._device_name:
            return self._device_name
        return connection_manager.config.host

    # ----------------------------------------------------------- handlers
    def _on_connected_changed(self, connected: bool) -> None:
        if not connected:
            self._device_name = None
            self._last_handler_status = {}
        self._refresh_device_name()
        self._refresh_connection_label(connected)
        self._refresh_mode_chip()

    def _on_connection_device_changed(self, _cfg: object) -> None:
        dev = connection_manager.active_device
        if dev and (dev.device_name or "").strip():
            self._device_name = (dev.device_name or "").strip()
        self._refresh_device_name()
        self._refresh_connection_label(connection_manager.connected)

    def _on_handler_status(self, payload: Dict[str, Any]) -> None:
        self._last_handler_status = dict(payload or {})
        mode_norm = _normalize_handler_mode(payload.get("mode"))
        if mode_norm is not None and app_state.mode != mode_norm:
            app_state.set_mode(mode_norm)
        self._refresh_mode_chip()

    def _refresh_device_name(self) -> None:
        if not connection_manager.connected:
            self._device_name = None
            self._refresh_connection_label(False)
            return
        if not api_client.available:
            self._refresh_connection_label(connection_manager.connected)
            return
        try:
            cfg = api_client.client.get_handler_config()  # type: ignore[union-attr]
        except Exception:
            cfg = None
        if not isinstance(cfg, dict):
            self._refresh_connection_label(connection_manager.connected)
            return
        name = cfg.get("device_name")
        self._device_name = (str(name).strip() if name else None) or None
        active = connection_manager.active_device
        if active:
            incoming = (self._device_name or "").strip()
            stored = (active.device_name or "").strip()
            if incoming != stored:
                connection_manager.update_device(active.host, device_name=incoming)
        self._refresh_connection_label(connection_manager.connected)

    def _refresh_mode_chip(self) -> None:
        p = self._last_handler_status
        raw_mode = (p.get("mode") or "").strip()
        wf = (p.get("current_workflow") or "").strip()

        mode_norm = _normalize_handler_mode(raw_mode)
        if mode_norm is not None:
            pretty = (
                "MAINTENANCE"
                if mode_norm == MODE_MAINTENANCE
                else "PRODUCTION"
            )
        else:
            pretty = (
                "MAINTENANCE"
                if app_state.mode == MODE_MAINTENANCE
                else "PRODUCTION"
            )

        if wf:
            text = f"MODE · {pretty} · {wf}"
        else:
            text = f"MODE · {pretty}"

        self.mode_chip.setText(text)
        chip_mode = (
            MODE_MAINTENANCE
            if pretty == "MAINTENANCE"
            else MODE_PRODUCTION
        )
        self.mode_chip.setProperty("mode", chip_mode)
        style = self.mode_chip.style()
        style.unpolish(self.mode_chip)
        style.polish(self.mode_chip)

        self.mode_btn.setText(
            "Switch to Production"
            if app_state.mode == MODE_MAINTENANCE
            else "Switch to Maintenance"
        )

    def _on_status_changed(self, statuses: dict) -> None:
        self._refresh_connection_label(connection_manager.connected, statuses)

    def _refresh_connection_label(
        self, is_connected: bool, statuses: dict | None = None
    ) -> None:
        host = connection_manager.config.host
        if not is_connected:
            self.connection_btn.setText("  Not connected")
            self.connection_btn.setToolTip("")
            return
        if statuses is None:
            statuses = connection_manager.statuses
        ok = [s for s, st in statuses.items() if st == "ok"]
        total = len(statuses) or 1
        label = self._device_name if self._device_name else host
        tip = f"{host}" if self._device_name else ""
        self.connection_btn.setToolTip(tip)
        if len(ok) == total:
            self.connection_btn.setText(f"  {label}")
        elif ok:
            self.connection_btn.setText(f"  {label} — {len(ok)}/{total}")
        else:
            self.connection_btn.setText(f"  Connecting…  ({label})")

    def _on_notifications_changed(self) -> None:
        self.notif_btn.setText(f"  {notification_manager.unread_count}")

    def _on_account_changed(self) -> None:
        acct = account_manager.account
        self.account_btn.setText(f"  @ {acct.username}" if acct else "  Sign In")
