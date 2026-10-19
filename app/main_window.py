"""Application main window."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.constants import APP_NAME, MODE_MAINTENANCE, MODE_PRODUCTION
from app.icons import icon
from app.managers import (
    api_client,
    commander,
    connection_manager,
    local_camera_server,
    notification_manager,
    status_manager,
)
from app.managers.local_manager import local_manager

log = logging.getLogger("MainWindow")

try:
    from cnode_manager_sdk import CNodeManagerError
except ImportError:  # pragma: no cover
    CNodeManagerError = Exception  # type: ignore[assignment]
from app.pages import (
    CamerasPage,
    DashboardPage,
    DetectionProfilesPage,
    IOTestPage,
    LiveDetectionPage,
    UpdatesPage,
    WorkflowsPage,
)
from app.state import app_state
from app.widgets.anchor_flyout import AnchorFlyout
from app.widgets.popups import (
    AboutDialog,
    AccountFlyoutPanel,
    ConnectedServicesDialog,
    ConnectionDialog,
    HelpDialog,
    NotificationsFlyoutPanel,
    SettingsDialog,
)
from app.widgets.sidebar import Sidebar
from app.widgets.topbar import TopBar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self._local_camera_server = local_camera_server.LocalCameraServer()

        self._build_menu()

        central = QWidget()
        central.setObjectName("centralHost")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.topbar = TopBar()
        self.topbar.mode_toggle_requested.connect(self._toggle_mode)
        self.topbar.connection_clicked.connect(self._open_connection)

        self._notif_flyout = AnchorFlyout(
            self,
            self.topbar.notif_btn,
            min_width=360,
            max_height=500,
            show_without_activating=True,
        )
        self._notif_flyout.set_content(NotificationsFlyoutPanel())
        self._account_flyout = AnchorFlyout(
            self,
            self.topbar.account_btn,
            min_width=340,
            max_height=400,
            show_without_activating=False,
        )
        self._account_flyout.set_title("Account")
        self._account_flyout.set_content(AccountFlyoutPanel())
        self._notif_flyout.about_to_show.connect(self._account_flyout.hide)
        self._account_flyout.about_to_show.connect(self._notif_flyout.hide)

        self.topbar.notifications_clicked.connect(self._notif_flyout.show_for_anchor_click)
        self.topbar.account_clicked.connect(self._account_flyout.show_for_anchor_click)
        outer.addWidget(self.topbar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.page_selected.connect(self._on_page_selected)
        body_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack, 1)

        self._pages: dict[str, QWidget] = {}
        for key, page in {
            "dashboard": DashboardPage(),
            "live_detection": LiveDetectionPage(),
            "workflows": WorkflowsPage(),
            "detection_profiles": DetectionProfilesPage(),
            "cameras": CamerasPage(),
            "io_test": IOTestPage(),
            "updates": UpdatesPage(),
        }.items():
            self._pages[key] = page
            self.stack.addWidget(page)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status = status
        connection_manager.status_changed.connect(self._update_status_bar)
        self._update_status_bar(connection_manager.statuses)

        app_state.mode_changed.connect(self._on_mode_changed)

        self.sidebar.page_selected.emit("dashboard")

        # Nothing networks until the user explicitly connects.
        connection_manager.connected_changed.connect(self._on_connected_changed)
        connection_manager.connection_changed.connect(self._on_connection_target_changed)

        notification_manager.add(
            "Welcome",
            "Not connected yet. Click the connection chip (top bar) to set a host and "
            "connect to your cnode device.",
            level="info",
        )

    # ----------------------------------------------------------------- UI
    def _build_menu(self) -> None:
        menu = self.menuBar()

        conn_menu = menu.addMenu("&Connection")
        self._conn_menu = conn_menu
        open_conn = QAction(icon("connection"), "Configure connection…", self)
        open_conn.setShortcut(QKeySequence("Ctrl+K"))
        open_conn.triggered.connect(self._open_connection)
        conn_menu.addAction(open_conn)

        refresh_conn = QAction(icon("refresh"), "Refresh statuses", self)
        refresh_conn.setShortcut(QKeySequence("F5"))
        refresh_conn.triggered.connect(connection_manager.refresh_statuses)
        conn_menu.addAction(refresh_conn)

        self._action_connected_services = QAction(
            icon("info"), "Connected services…", self
        )
        self._action_connected_services.setVisible(False)
        self._action_connected_services.triggered.connect(
            self._open_connected_services
        )
        conn_menu.addAction(self._action_connected_services)

        conn_menu.aboutToShow.connect(self._update_connection_menu_dev_actions)

        settings_menu = menu.addMenu("&Settings")
        settings = QAction(icon("settings"), "Preferences…", self)
        settings.triggered.connect(self._open_settings)
        settings_menu.addAction(settings)

        help_menu = menu.addMenu("&Help")
        help_action = QAction(icon("help"), "Help", self)
        help_action.setShortcut(QKeySequence("F1"))
        help_action.triggered.connect(self._open_help)
        help_menu.addAction(help_action)

        about_menu = menu.addMenu("&About")
        about = QAction(icon("info"), "About…", self)
        about.triggered.connect(self._open_about)
        about_menu.addAction(about)

    # ------------------------------------------------------------- Actions
    def _on_page_selected(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        if key == "dashboard":
            refresh = getattr(page, "refresh_compose_caches", None)
            if callable(refresh):
                refresh()

    def start_live_detection(self) -> None:
        """Open the detection workspace and start its configured camera feed."""
        self.sidebar.select_page("live_detection")
        page = self._pages.get("live_detection")
        start = getattr(page, "start_detection", None)
        if callable(start):
            start()

    def _toggle_mode(self) -> None:
        if app_state.mode == MODE_PRODUCTION:
            if not api_client.available:
                self._ensure_local_cnode()
                app_state.set_mode(MODE_MAINTENANCE)
                page = self._pages.get("live_detection")
                shutdown = getattr(page, "shutdown", None)
                if callable(shutdown):
                    shutdown()
                self.sidebar.select_page("dashboard")
                return
            # Going to maintenance: always run the calibration workflow.
            self._switch_mode_to(MODE_MAINTENANCE, wf_identifier="calibration")
            return

        # Coming back to production: ask the user which workflow to run.
        choice = self._pick_production_workflow()
        if choice is None:
            return  # user cancelled; stay in maintenance
        wf_name, wf_type = choice
        # The handler identifies workflows by their unique ``name`` and
        # resolves the matching runner class via the ``type`` field in
        # workflows.json, so we always send the name here.
        self._switch_mode_to(
            MODE_PRODUCTION,
            wf_identifier=wf_name,
            display_name=f"{wf_name} ({wf_type})" if wf_type else wf_name,
        )

    def _pick_production_workflow(self) -> Optional[Tuple[str, str]]:
        if not api_client.available:
            choices = [(name, "local template matching") for name in local_manager.list_workflows() if name != "calibration"]
            if not choices:
                choices = [("Local inspection", "local template matching")]
            dlg = _WorkflowPickDialog(choices, parent=self)
            if dlg.exec() != QDialog.Accepted:
                return None
            return dlg.selected()
        try:
            raw = api_client.client.list_workflows() or {}
        except CNodeManagerError as exc:
            QMessageBox.warning(
                self, "Couldn't load workflows", f"cnode_manager is unreachable:\n{exc}"
            )
            return None

        choices: List[Tuple[str, str]] = []
        for name, entry in raw.items():
            wf_type = ""
            if isinstance(entry, dict):
                wf_type = str(entry.get("type") or entry.get("workflow_type") or "")
            if name == "calibration":
                continue  # calibration is only used for maintenance mode
            choices.append((name, wf_type))
        if not choices:
            QMessageBox.information(
                self,
                "No production workflows",
                "cnode_manager has no non-calibration workflows yet. Create one "
                "on the Workflows page first.",
            )
            return None

        dlg = _WorkflowPickDialog(choices, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.selected()

    def _switch_mode_to(
        self,
        mode: str,
        *,
        wf_identifier: str,
        display_name: Optional[str] = None,
    ) -> None:
        """Switch workflow with a single start-and-run command."""
        if not api_client.available:
            self._ensure_local_cnode()
            local_manager.set_active_workflow(wf_identifier)
            notification_manager.add(
                "Local workflow",
                f"Running <b>{display_name or wf_identifier}</b> with this laptop's camera and saved detection templates.",
                level="info",
            )
            app_state.set_mode(mode)
            if mode == MODE_PRODUCTION:
                self.start_live_detection()
            return
        if not connection_manager.connected:
            QMessageBox.information(
                self,
                "Not connected",
                "Connect to a cnode device before switching modes.",
            )
            return

        pretty = display_name or wf_identifier
        log.info(
            "Switching to %s mode via workflow '%s' (identifier=%s)",
            mode,
            pretty,
            wf_identifier,
        )
        notification_manager.add(
            "Switching mode",
            f"Switching workflow to <b>{pretty}</b>…",
            level="info",
        )

        ok = commander.switch_workflow(wf_identifier)
        if not ok:
            QMessageBox.warning(
                self,
                "Switch failed",
                "Could not send workflow.start_and_run command.",
            )
            return
        app_state.set_mode(mode)

    def _ensure_local_cnode(self) -> None:
        """Activate the laptop camera endpoint for offline manager mode."""
        if not connection_manager.connected or connection_manager.config.host.strip().lower() not in {"localhost", "127.0.0.1", "::1"}:
            connection_manager.connect("localhost")
        self._local_camera_server.start()

    def _on_mode_changed(self, mode: str) -> None:
        if mode == MODE_MAINTENANCE:
            notification_manager.add(
                "Maintenance mode",
                "Configure pages are now available.",
                level="warning",
            )

    def _update_status_bar(self, statuses: dict) -> None:
        host = connection_manager.config.host
        if not connection_manager.connected:
            self._status.showMessage(f"Host: {host}   |   Not connected")
            return
        ok = [s for s, st in statuses.items() if st == "ok"]
        total = len(statuses) or 1
        label = self.topbar.device_display_name
        head = f"{label}  ·  {host}" if label != host else host
        self._status.showMessage(
            f"{head}   |   Services online: {len(ok)}/{total}"
        )

    def _on_connected_changed(self, is_connected: bool) -> None:
        if is_connected:
            if connection_manager.config.host.strip().lower() in {
                "localhost", "127.0.0.1", "::1"
            }:
                self._local_camera_server.start()
            status_manager.start()
            notification_manager.add(
                "Connected",
                f"Connected to {connection_manager.config.host}.",
                level="info",
            )
        else:
            self._local_camera_server.stop()
            status_manager.stop()
            notification_manager.add(
                "Disconnected",
                "Disconnected from device. Streams and live data paused.",
                level="info",
            )
        self._update_status_bar(connection_manager.statuses)

    def _on_connection_target_changed(self, config) -> None:
        """Keep the laptop camera stream available when switching to local mode."""
        if config.host.strip().lower() in {"localhost", "127.0.0.1", "::1"}:
            self._local_camera_server.start()

    # ------------------------------------------------------------- Popups
    def _update_connection_menu_dev_actions(self) -> None:
        """Show *Connected services…* only when cnode_handler.json has ``dev: true``."""
        show = False
        if connection_manager.connected and api_client.available:
            try:
                cfg = api_client.client.read_cnode_handler_json()
                show = cfg.get("dev") is True
            except CNodeManagerError:
                show = False
        self._action_connected_services.setVisible(show)

    def _open_connected_services(self) -> None:
        ConnectedServicesDialog(self).exec()

    def _open_connection(self) -> None:
        ConnectionDialog(self).exec()

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _open_help(self) -> None:
        HelpDialog(self).exec()

    def _open_about(self) -> None:
        AboutDialog(self).exec()

    # ----------------------------------------------------------- Lifecycle
    def closeEvent(self, event) -> None:  # noqa: N802
        self._notif_flyout.hide()
        self._account_flyout.hide()
        connection_manager.disconnect()
        self._local_camera_server.stop()
        status_manager.stop()
        live_detection = self._pages.get("live_detection")
        shutdown = getattr(live_detection, "shutdown", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Production workflow picker
# ---------------------------------------------------------------------------

class _WorkflowPickDialog(QDialog):
    """Lets the user pick which workflow cnode_handler should run in production."""

    def __init__(
        self,
        choices: List[Tuple[str, str]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick production workflow")
        self.setModal(True)
        self.resize(460, 360)
        self._choices: List[Tuple[str, str]] = list(choices)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(
            "Select the workflow the device should run when returning to "
            "production mode. The handler will stop, switch to the chosen "
            "workflow, and start running."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        root.addWidget(hint)

        self._list = QListWidget()
        for name, wf_type in self._choices:
            pretty_type = wf_type or "(unknown type)"
            item = QListWidgetItem(f"{name}   \u2014   {pretty_type}")
            item.setIcon(icon("workflows"))
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        root.addWidget(self._list, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = btns.button(QDialogButtonBox.Ok)
        ok_btn.setText("  Switch")
        ok_btn.setIcon(icon("mode", color="#FFFFFF"))
        ok_btn.setProperty("accent", True)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def selected(self) -> Optional[Tuple[str, str]]:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._choices):
            return None
        return self._choices[row]
