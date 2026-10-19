"""Common dialogs / popups."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.constants import APP_NAME, PROBED_SERVICES
from app.icons import icon
from app.managers import account_manager, connection_manager, notification_manager
from app.state import app_state


# Human labels for probed TCP services (same keys as PROBED_SERVICES).
_SERVICE_LABELS = {
    "camera_stream": "Camera stream",
    "detection_stream": "Detection stream",
    "rabbitmq": "RabbitMQ",
    "cnode_manager": "cnode_manager API",
}


class _ConnectWorker(QObject):
    """Runs connect() off the UI thread so the dialog remains responsive."""

    finished = Signal()
    failed = Signal(str)

    def __init__(self, host: str) -> None:
        super().__init__()
        self._host = host

    def run(self) -> None:
        try:
            connection_manager.connect(self._host)
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(str(exc))
            return
        self.finished.emit()


# ---------------------------------------------------------------------------
# Connection dialog
# ---------------------------------------------------------------------------

class ConnectionDialog(QDialog):
    """Device-list connection UI.

    Shows every previously-connected cnode device. The user picks one and
    clicks **Connect**, or adds a new device by typing its hostname.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connection")
        self.setModal(True)
        self.resize(460, 520)
        self._connect_pending = False
        self._connect_timeout_ms = 8000
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)
        self._connect_thread: Optional[QThread] = None
        self._connect_worker: Optional[_ConnectWorker] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Connect to a cnode device")
        title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        root.addWidget(title)

        hint = QLabel(
            "Select a previously-used device, or add a new one by typing its "
            "hostname and clicking <b>Add</b>."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Device list
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setMinimumHeight(180)
        self._list.itemDoubleClicked.connect(lambda _it: self._on_connect())
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._list, 1)

        # Add-device row
        add_row = QHBoxLayout()
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("cnode1.local")
        self.host_edit.returnPressed.connect(self._on_add)
        add_row.addWidget(self.host_edit, 1)
        self.add_btn = QPushButton("  Add")
        self.add_btn.setIcon(icon("plus"))
        self.add_btn.setIconSize(QSize(16, 16))
        self.add_btn.clicked.connect(self._on_add)
        add_row.addWidget(self.add_btn)
        self.forget_btn = QPushButton("  Forget")
        self.forget_btn.setIcon(icon("trash"))
        self.forget_btn.setIconSize(QSize(16, 16))
        self.forget_btn.clicked.connect(self._on_forget)
        add_row.addWidget(self.forget_btn)
        root.addLayout(add_row)

        self._footer_note = QLabel()
        self._footer_note.setObjectName("Muted")
        self._footer_note.setWordWrap(True)
        root.addWidget(self._footer_note)

        self._connect_progress = QProgressBar()
        self._connect_progress.setRange(0, 100)
        self._connect_progress.setValue(0)
        self._connect_progress.setTextVisible(True)
        self._connect_progress.setFormat("Connecting… %p%")
        self._connect_progress.setMinimumHeight(16)
        self._connect_progress.setStyleSheet(
            "QProgressBar {"
            "  border: 1px solid #CBD5E1;"
            "  border-radius: 6px;"
            "  background: #F1F5F9;"
            "  color: #111827;"
            "  text-align: center;"
            "}"
            "QProgressBar::chunk {"
            "  background: #2563EB;"
            "  border-radius: 5px;"
            "}"
        )
        self._connect_progress.setVisible(False)
        root.addWidget(self._connect_progress)

        # Buttons
        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("  Connect")
        self.connect_btn.setProperty("accent", True)
        self.connect_btn.setIcon(icon("connection", color="#FFFFFF"))
        self.connect_btn.setIconSize(QSize(16, 16))
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn = QPushButton("  Disconnect")
        self.disconnect_btn.setIcon(icon("close"))
        self.disconnect_btn.setIconSize(QSize(16, 16))
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

        # Signals
        connection_manager.devices_changed.connect(self._rebuild_list)
        connection_manager.connection_changed.connect(lambda _c: self._rebuild_list())
        connection_manager.connected_changed.connect(self._on_connected_changed)
        connection_manager.status_changed.connect(self._on_status_changed)

        self._rebuild_list()
        self._on_connected_changed(connection_manager.connected)

    # ----------------------------------------------------------- helpers
    def _overall_status(self, host: str) -> str:
        active = connection_manager.active_device
        if not active or active.host.lower() != host.lower():
            return "saved"
        if not connection_manager.connected:
            return "idle"
        statuses = connection_manager.statuses
        ok = sum(1 for v in statuses.values() if v == "ok")
        total = len(statuses) or 1
        if ok == 0:
            return "unreachable"
        if ok == total:
            return "connected"
        return f"partial ({ok}/{total})"

    def _rebuild_list(self) -> None:
        active_host = (
            connection_manager.active_device.host
            if connection_manager.active_device
            else None
        )
        selected_host = self._selected_host() or active_host
        self._list.blockSignals(True)
        self._list.clear()
        for dev in connection_manager.devices():
            state = self._overall_status(dev.host)
            marker = {
                "connected": "🟢",
                "partial": "🟡",
                "unreachable": "🔴",
                "idle": "⚪",
                "saved": "⚪",
            }.get(state.split(" ", 1)[0], "⚪")
            primary = dev.display_name
            label_bits = [marker, primary]
            if dev.host and primary.casefold() != dev.host.casefold():
                label_bits.append(f"({dev.host})")
            label_bits.append("—")
            label_bits.append(state)
            item = QListWidgetItem("  ".join(label_bits))
            item.setData(Qt.UserRole, dev.host)
            self._list.addItem(item)
            if selected_host and dev.host.lower() == selected_host.lower():
                item.setSelected(True)
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)
        self._on_selection_changed()

    def _selected_host(self) -> Optional[str]:
        items = self._list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    # ----------------------------------------------------------- callbacks
    def _on_selection_changed(self) -> None:
        host = self._selected_host()
        can_connect = bool(host) and not self._connect_pending and not connection_manager.connected
        self.connect_btn.setEnabled(can_connect)
        self.forget_btn.setEnabled(bool(host))

    def _on_connected_changed(self, is_connected: bool) -> None:
        self.disconnect_btn.setEnabled(is_connected)
        if not is_connected:
            self._stop_connect_progress()
        if is_connected and connection_manager.active_device is not None:
            dev = connection_manager.active_device
            shown = dev.display_name
            self._footer_note.setText(
                f"Connected to <b>{shown}</b>. Live data is now flowing."
            )
        else:
            self._footer_note.setText(
                "Not connected. No network calls are made until you click Connect."
            )
        self._rebuild_list()

    def _on_status_changed(self, _statuses: object) -> None:
        if self._connect_pending:
            self._stop_connect_progress()
        self._rebuild_list()

    def _on_add(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Host required", "Enter a hostname first.")
            return
        connection_manager.add_device(host)
        self.host_edit.clear()
        # Auto-select the new device
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.UserRole).lower() == host.lower():
                self._list.setCurrentItem(item)
                break

    def _on_connect(self) -> None:
        if self._connect_pending:
            return
        host = self._selected_host()
        if not host:
            QMessageBox.warning(self, "Pick a device", "Select a device first.")
            return
        self._connect_pending = True
        self._start_connect_progress()
        self._footer_note.setText(f"Connecting to <b>{host}</b>…")
        self.connect_btn.setEnabled(False)
        self._connect_timer.start(self._connect_timeout_ms)
        self._start_connect_worker(host)

    def _start_connect_progress(self) -> None:
        # Indeterminate bar (min == max) — no fake percent that sits at 8% or
        # wraps while the real connect finishes at unpredictable speed.
        self._connect_progress.setRange(0, 0)
        self._connect_progress.setFormat("Connecting…")
        self._connect_progress.setVisible(True)

    def _stop_connect_progress(self) -> None:
        self._connect_pending = False
        self._connect_timer.stop()
        self._connect_progress.setRange(0, 100)
        self._connect_progress.setValue(0)
        self._connect_progress.setFormat("Connecting… %p%")
        self._connect_progress.setVisible(False)

    def _start_connect_worker(self, host: str) -> None:
        if self._connect_thread is not None:
            return
        thread = QThread(self)
        worker = _ConnectWorker(host)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_connect_worker_finished)
        worker.failed.connect(self._on_connect_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_connect_worker_teardown)
        self._connect_thread = thread
        self._connect_worker = worker
        thread.start()

    def _on_connect_worker_finished(self) -> None:
        # Keep UI state transitions centralized in connected_changed/timeout.
        pass

    def _on_connect_worker_failed(self, err: str) -> None:
        self._stop_connect_progress()
        self._footer_note.setText("Connection failed. Please try again.")
        self._on_selection_changed()
        QMessageBox.warning(self, "Connect failed", err)

    def _on_connect_worker_teardown(self) -> None:
        self._connect_thread = None
        self._connect_worker = None

    def _on_disconnect(self) -> None:
        self._stop_connect_progress()
        connection_manager.disconnect()

    def _on_connect_timeout(self) -> None:
        # Defensive timeout for cases where a connection transition signal
        # does not arrive (e.g. unexpected runtime failures).
        if connection_manager.connected:
            return
        self._stop_connect_progress()
        self._footer_note.setText(
            "Connection timed out. Check host/network and try again."
        )
        self._on_selection_changed()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_connect_progress()
        if self._connect_thread is not None:
            self._connect_thread.quit()
            self._connect_thread.wait(1000)
        super().closeEvent(event)

    def _on_forget(self) -> None:
        host = self._selected_host()
        if not host:
            return
        if len(connection_manager.devices()) <= 1:
            QMessageBox.information(
                self,
                "Keep at least one",
                "The device list must have at least one entry.",
            )
            return
        if (
            QMessageBox.question(
                self,
                "Forget device",
                f"Remove <b>{host}</b> from the saved devices list?",
            )
            != QMessageBox.Yes
        ):
            return
        connection_manager.remove_device(host)


# ---------------------------------------------------------------------------
# Connected services (dev builds — cnode_handler.json ``dev``: true)
# ---------------------------------------------------------------------------


class ConnectedServicesDialog(QDialog):
    """Shows reachability of probed TCP services on the active device."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connected services")
        self.setModal(True)
        self.resize(520, 320)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(
            "Socket reachability from this machine to the device (same probe as the "
            "status bar)."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        refresh = QPushButton("  Refresh")
        refresh.setIcon(icon("refresh"))
        refresh.setIconSize(QSize(16, 16))
        refresh.clicked.connect(self._populate)
        btn_row.addWidget(refresh)
        btn_row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        root.addLayout(btn_row)

        connection_manager.status_changed.connect(self._on_status_changed)
        connection_manager.refresh_statuses()
        self._populate()

    def _on_status_changed(self, _statuses: object) -> None:
        if self.isVisible():
            self._populate()

    def _populate(self) -> None:
        self._list.clear()
        dev = connection_manager.active_device
        if dev is None or not connection_manager.connected:
            it = QListWidgetItem("Not connected to a device.")
            it.setForeground(QBrush(QColor("#9CA3AF")))
            self._list.addItem(it)
            return

        host = dev.host
        statuses = connection_manager.statuses
        for key, default_port in PROBED_SERVICES.items():
            if key == "cnode_manager":
                port = int(getattr(dev, "manager_port", default_port))
            else:
                port = int(getattr(dev, f"{key}_port", default_port))
            label = _SERVICE_LABELS.get(key, key)
            st = statuses.get(key, "unknown")
            if st == "ok":
                sym, detail, color = "●", "reachable", "#16A34A"
            elif st == "bad":
                sym, detail, color = "●", "unreachable", "#DC2626"
            else:
                sym, detail, color = "○", "unknown", "#9CA3AF"
            line = f"{sym}  {label}  ·  {host}:{port}  —  {detail}"
            item = QListWidgetItem(line)
            item.setForeground(QBrush(QColor(color)))
            self._list.addItem(item)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            connection_manager.status_changed.disconnect(self._on_status_changed)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Account / notifications — content for top-bar hover flyouts
# ---------------------------------------------------------------------------


class AccountFlyoutPanel(QWidget):
    """Sign-in / signed-in summary for the floating account panel."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(10)
        account_manager.changed.connect(self._rebuild)
        self._rebuild()

    def _clear_root(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear_root()
        if account_manager.is_logged_in:
            acct = account_manager.account
            info = QLabel(
                f"<div style='font-size: 12pt;'>Signed in as <b>{acct.username}</b><br/>"
                f"<span style='color: #6B7280;'>Role: {acct.role}</span></div>"
            )
            info.setWordWrap(True)
            self._root.addWidget(info)
            logout = QPushButton("Sign out")
            logout.setProperty("accent", True)
            logout.clicked.connect(self._on_logout)
            self._root.addWidget(logout)
        else:
            form = QFormLayout()
            self.user_edit = QLineEdit()
            self.pass_edit = QLineEdit()
            self.pass_edit.setEchoMode(QLineEdit.Password)
            self.role_combo = QComboBox()
            self.role_combo.addItems(["operator", "maintainer", "admin"])
            form.addRow("Username", self.user_edit)
            form.addRow("Password", self.pass_edit)
            form.addRow("Role", self.role_combo)
            self._root.addLayout(form)
            sign_in = QPushButton("Sign in")
            sign_in.setProperty("accent", True)
            sign_in.clicked.connect(self._on_login)
            self._root.addWidget(sign_in)

    def _on_login(self) -> None:
        if not account_manager.login(
            self.user_edit.text().strip(),
            self.pass_edit.text(),
            self.role_combo.currentText(),
        ):
            QMessageBox.warning(
                self.window(), "Sign in", "Username and password are required."
            )

    def _on_logout(self) -> None:
        account_manager.logout()


class NotificationsFlyoutPanel(QWidget):
    """Minimal notification list for the floating panel (single clear icon)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 8)
        header.setSpacing(6)
        title = QLabel("Notifications")
        title.setStyleSheet(
            "font-size: 10pt; font-weight: 600; color: #6B7280; "
            "letter-spacing: 0.05em;"
        )
        self._clear_btn = QToolButton()
        self._clear_btn.setObjectName("NotificationClearButton")
        self._clear_btn.setToolTip("Clear all")
        self._clear_btn.setAutoRaise(True)
        self._clear_btn.setIcon(icon("trash", color="#6B7280", size=18))
        self._clear_btn.setIconSize(QSize(18, 18))
        self._clear_btn.setFixedSize(30, 30)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(notification_manager.clear)
        header.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        header.addWidget(self._clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(header)

        self._list = QListWidget()
        self._list.setObjectName("NotificationFlyoutList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setMinimumHeight(160)

        scroll = QScrollArea()
        scroll.setObjectName("NotificationFlyoutScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._list)
        scroll.setMaximumHeight(340)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        notification_manager.changed.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        self._list.clear()
        items = notification_manager.items
        self._clear_btn.setVisible(bool(items))
        if not items:
            empty = QListWidgetItem("No notifications")
            empty.setForeground(QBrush(QColor("#9CA3AF")))
            self._list.addItem(empty)
            return
        for n in items:
            ts = n.timestamp.strftime("%H:%M")
            line = f"{ts}  {n.title}\n{n.message}"
            self._list.addItem(QListWidgetItem(line))


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """Advanced / rarely-changed settings (service ports, RMQ credentials)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(420, 360)

        cfg = connection_manager.config
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        intro = QLabel(
            "Advanced settings. Ports default to the standard cnode stack and rarely "
            "need to be changed."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.camera_port = self._spin(cfg.camera_stream_port)
        self.detect_port = self._spin(cfg.detection_stream_port)
        self.supervisor_port = self._spin(cfg.supervisor_port)
        self.rmq_port = self._spin(cfg.rabbitmq_port)
        self.manager_port = self._spin(cfg.manager_port)
        self.rmq_user = QLineEdit(cfg.rmq_username)
        self.rmq_pass = QLineEdit(cfg.rmq_password)
        self.rmq_pass.setEchoMode(QLineEdit.Password)
        self.rmq_vhost = QLineEdit(cfg.rmq_vhost)

        form.addRow("Camera stream port", self.camera_port)
        form.addRow("Detection stream port", self.detect_port)
        form.addRow("Supervisor port", self.supervisor_port)
        form.addRow("RabbitMQ port", self.rmq_port)
        form.addRow("Manager API port", self.manager_port)
        form.addRow("RMQ user", self.rmq_user)
        form.addRow("RMQ password", self.rmq_pass)
        form.addRow("RMQ vhost", self.rmq_vhost)
        root.addLayout(form)

        self.sidebar_override_toggle = QCheckBox(
            "Always show Configure + Device sections in sidebar"
        )
        self.sidebar_override_toggle.setChecked(app_state.sidebar_visibility_override)
        root.addWidget(self.sidebar_override_toggle)

        root.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    @staticmethod
    def _spin(value: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(1, 65535)
        s.setValue(value)
        return s

    def _save(self) -> None:
        connection_manager.update(
            camera_stream_port=self.camera_port.value(),
            detection_stream_port=self.detect_port.value(),
            supervisor_port=self.supervisor_port.value(),
            rabbitmq_port=self.rmq_port.value(),
            manager_port=self.manager_port.value(),
            rmq_username=self.rmq_user.text().strip(),
            rmq_password=self.rmq_pass.text(),
            rmq_vhost=self.rmq_vhost.text().strip() or "/",
        )
        app_state.set_sidebar_visibility_override(self.sidebar_override_toggle.isChecked())
        self.accept()


# ---------------------------------------------------------------------------
# Help / About dialogs
# ---------------------------------------------------------------------------

class HelpDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Help")
        self.resize(520, 420)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        body = QTextBrowser()
        body.setHtml(
            """
            <h2>CNODE Configurer</h2>
            <p>Use the sidebar to navigate between:</p>
            <ul>
              <li><b>Dashboard</b> — live inputs, outputs, and camera streams.</li>
              <li><b>Configure</b> (in Maintenance mode) — Workflows, Detection Profiles, Cameras.</li>
              <li><b>Updates</b> — Device and application updates.</li>
            </ul>
            <p>Use the connection button in the top bar to set the cnode device host and ports.
            Switch to <b>Maintenance</b> mode to enable Configure pages.</p>
            """
        )
        root.addWidget(body)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        root.addWidget(btns)


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        self.resize(360, 200)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        frame = QFrame()
        frame.setObjectName("Card")
        inner = QVBoxLayout(frame)
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 16pt; font-weight: 800;")
        desc = QLabel(
            "Configuration front-end for the cnode_handler industrial vision service."
        )
        desc.setWordWrap(True)
        version = QLabel("Version 0.1.0")
        version.setObjectName("Muted")
        inner.addWidget(title)
        inner.addWidget(desc)
        inner.addWidget(version)
        root.addWidget(frame)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        root.addWidget(btns)
