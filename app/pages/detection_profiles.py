"""Detection Profiles configuration page.

Layout:
* Left panel — workflow selector on top, then the list of profiles for the
  selected workflow.
* Right panel — editor with the **Capture template** tool inline (camera +
  button) above a templates list (thumbnails + name) and the usual
  algorithm/threshold fields.

Perspective transforms live on the Workflows page (per workflow + camera).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import requests
from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.icons import icon
from app.constants import ROOT_DIR
from app.managers import api_client, connection_manager, local_camera_server
from app.managers.local_camera_config import local_camera_config
from app.managers.local_manager import local_manager
from app.pages._common import PageBase, PageHeader, primary_button, secondary_button
from app.pages.cameras import _TemplateCapture
from app.widgets.card import Card

log = logging.getLogger("DetectionProfilesPage")

try:
    from cnode_manager_sdk import (
        CNodeManagerError,
        DetectionProfile,
        DetectionTemplate,
        Workflow,
    )
except ImportError:  # pragma: no cover
    CNodeManagerError = Exception  # type: ignore[assignment]
    DetectionProfile = None  # type: ignore[assignment]
    DetectionTemplate = None  # type: ignore[assignment]
    Workflow = None  # type: ignore[assignment]


ALGORITHMS = ["template_matching", "yoloe_detection", "detect_pass"]
MATCH_METHODS = [
    "TM_CCOEFF_NORMED",
    "TM_CCOEFF",
    "TM_SQDIFF",
    "TM_SQDIFF_NORMED",
    "TM_CCORR",
    "TM_CCORR_NORMED",
]

DATA_DIR = ROOT_DIR.parent / "DATA"


# ---------------------------------------------------------------------------
# Background thumbnail fetcher
# ---------------------------------------------------------------------------

class _ThumbnailLoader(QObject):
    """Fetches template thumbnails off the GUI thread."""

    loaded = Signal(int, QImage)  # (template_id, image)
    failed = Signal(int, str)

    def __init__(
        self,
        template_id: int,
        urls: List[str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.template_id = template_id
        self.urls = [u for u in urls if isinstance(u, str) and u.strip()]

    def run(self) -> None:
        if not self.urls:
            self.failed.emit(self.template_id, "no thumbnail url")
            return
        last_err = "thumbnail fetch failed"
        for url in self.urls:
            try:
                resp = requests.get(url, timeout=4.0)
                resp.raise_for_status()
                img = QImage.fromData(resp.content)
                if img.isNull():
                    last_err = "not an image"
                    continue
                self.loaded.emit(self.template_id, img)
                return
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
        self.failed.emit(self.template_id, last_err)


# ---------------------------------------------------------------------------
# Template list item (thumbnail + name)
# ---------------------------------------------------------------------------

class _TemplateCard(QFrame):
    removed = Signal(int)

    THUMB_SIZE = 56

    def __init__(self, template: "DetectionTemplate", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TemplateCard")
        self._template = template

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(10)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(self.THUMB_SIZE, self.THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        placeholder = QPixmap(self.THUMB_SIZE, self.THUMB_SIZE)
        placeholder.fill(QColor("#E5E7EB"))
        self.thumb_label.setPixmap(placeholder)
        self.thumb_label.setStyleSheet(
            "background: #E5E7EB; border-radius: 6px;"
        )
        row.addWidget(self.thumb_label)

        name = (template.name or "").strip()
        if not name:
            # Fall back to the filename (without extension).
            base = (template.path or "").rsplit("/", 1)[-1]
            name = base.rsplit(".", 1)[0] if "." in base else base
        self.name_label = QLabel(name or f"template_{template.id}")
        self.name_label.setStyleSheet("font-weight: 600;")
        row.addWidget(self.name_label, 1)

        del_btn = QPushButton()
        del_btn.setIcon(icon("trash"))
        del_btn.setIconSize(QSize(16, 16))
        del_btn.setToolTip("Delete this template")
        del_btn.setFixedWidth(32)
        del_btn.clicked.connect(lambda: self.removed.emit(template.id))
        row.addWidget(del_btn)

    def set_thumbnail(self, image: QImage) -> None:
        pm = QPixmap.fromImage(image).scaled(
            self.THUMB_SIZE,
            self.THUMB_SIZE,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.thumb_label.setPixmap(pm)


# ---------------------------------------------------------------------------
# Details panel
# ---------------------------------------------------------------------------

class _ProfileDetails(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._profile: Optional["DetectionProfile"] = None
        self._cameras: List[str] = []
        self._templates: List["DetectionTemplate"] = []
        self._thumb_threads: List[QThread] = []
        self._local_mode = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QLabel("Select a detection profile")
        self.header.setStyleSheet("font-size: 14pt; font-weight: 700;")
        self.header.setContentsMargins(16, 16, 16, 16)
        root.addWidget(self.header)

        # Edit form with inline template capture + templates list.
        form_card = Card(title="Edit settings", icon_name="edit")
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.algo_combo = QComboBox()
        self.algo_combo.addItems(ALGORITHMS)
        self.method_combo = QComboBox()
        self.method_combo.addItems(MATCH_METHODS)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setDecimals(2)
        self.default_check = QCheckBox("Default for this workflow")
        self.multi_check = QCheckBox("Allow multiple matches")

        # Capture tools row — inline right above the templates list.
        capture_row = QHBoxLayout()
        capture_row.setContentsMargins(0, 0, 0, 0)
        capture_row.setSpacing(8)
        capture_row.addWidget(QLabel("Camera:"))
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(150)
        capture_row.addWidget(self.camera_combo, 0)
        self.capture_btn = primary_button("  Capture template\u2026", "crop")
        self.capture_btn.clicked.connect(self._open_capture)
        capture_row.addWidget(self.capture_btn)
        capture_row.addStretch(1)
        capture_widget = QWidget()
        capture_widget.setLayout(capture_row)

        # Templates list (thumbnails + name)
        self._templates_host = QWidget()
        self._templates_layout = QVBoxLayout(self._templates_host)
        self._templates_layout.setContentsMargins(0, 0, 0, 0)
        self._templates_layout.setSpacing(4)
        self._templates_empty = QLabel("No templates yet — capture one above.")
        self._templates_empty.setObjectName("Muted")
        self._templates_layout.addWidget(self._templates_empty)

        templates_scroll = QScrollArea()
        templates_scroll.setWidgetResizable(True)
        templates_scroll.setFrameShape(QFrame.NoFrame)
        templates_scroll.setMinimumHeight(140)
        templates_scroll.setMaximumHeight(240)
        templates_scroll.setWidget(self._templates_host)

        form.addRow("Name", self.name_edit)
        form.addRow("Algorithm", self.algo_combo)
        form.addRow("Match method", self.method_combo)
        form.addRow("Threshold", self.threshold_spin)
        form.addRow("Capture", capture_widget)
        form.addRow("Templates", templates_scroll)
        form.addRow(self.default_check)
        form.addRow(self.multi_check)
        form_card.body().addLayout(form)

        save_row = QHBoxLayout()
        self.save_btn = primary_button("  Save", "save")
        self.save_btn.clicked.connect(self._save)
        self.delete_btn = secondary_button("  Delete", "trash")
        self.delete_btn.clicked.connect(self._delete)
        save_row.addWidget(self.save_btn)
        save_row.addWidget(self.delete_btn)
        save_row.addStretch(1)
        form_card.body().addLayout(save_row)
        root.addWidget(form_card)

        perspective_hint = QLabel(
            "Perspective transforms are configured on the Workflows page "
            "(per camera, per workflow)."
        )
        perspective_hint.setObjectName("Muted")
        perspective_hint.setWordWrap(True)
        # root.addWidget(perspective_hint)

        # root.addStretch(1)

        self._set_editable(False)

    # ------------------------------------------------------------- state
    def profile(self) -> Optional["DetectionProfile"]:
        return self._profile

    def set_cameras(self, camera_ids: List[str]) -> None:
        self._cameras = list(camera_ids)
        previous = self.camera_combo.currentText()
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        for cid in self._cameras:
            self.camera_combo.addItem(icon("cameras"), cid)
        if previous and previous in self._cameras:
            self.camera_combo.setCurrentText(previous)
        self.camera_combo.blockSignals(False)

    def set_local_mode(self, enabled: bool) -> None:
        self._local_mode = bool(enabled)

    def set_profile(self, profile: Optional["DetectionProfile"]) -> None:
        self._profile = profile
        if profile is None:
            self.header.setText("Select a detection profile")
            self.name_edit.setText("")
            self.threshold_spin.setValue(0.5)
            self.algo_combo.setCurrentIndex(0)
            self.method_combo.setCurrentIndex(0)
            self.default_check.setChecked(False)
            self.multi_check.setChecked(False)
            self._set_templates([])
            self._set_editable(False)
            return

        self.header.setText(profile.name)
        self.name_edit.setText(profile.name)
        if profile.algorithm in ALGORITHMS:
            self.algo_combo.setCurrentText(profile.algorithm)
        method = (profile.data or {}).get("match_method", MATCH_METHODS[0])
        if method in MATCH_METHODS:
            self.method_combo.setCurrentText(method)
        self.threshold_spin.setValue(
            float((profile.data or {}).get("match_threshold", 0.5))
        )
        self.default_check.setChecked(profile.is_default)
        self.multi_check.setChecked(
            bool((profile.data or {}).get("multiple_matches", False))
        )
        self._set_editable(True)
        self._reload_templates()

    def _set_editable(self, enabled: bool) -> None:
        for w in (
            self.name_edit,
            self.algo_combo,
            self.method_combo,
            self.threshold_spin,
            self.default_check,
            self.multi_check,
            self.save_btn,
            self.delete_btn,
            self.capture_btn,
            self.camera_combo,
        ):
            w.setEnabled(enabled)

    # ----------------------------------------------------- templates
    def _reload_templates(self) -> None:
        if self._profile is None:
            self._set_templates([])
            return
        try:
            templates = local_manager.list_templates(self._profile.id) if self._local_mode else api_client.client.list_templates(profile_id=self._profile.id)
        except CNodeManagerError as exc:
            log.info("list_templates(profile=%s) failed: %s", self._profile.id, exc)
            templates = []
        self._set_templates(templates)

    def _set_templates(self, templates: List["DetectionTemplate"]) -> None:
        self._templates = list(templates)
        # Clear current rows
        while self._templates_layout.count():
            item = self._templates_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._templates_empty:
                w.deleteLater()
        # Stop old thumbnail threads
        for th in self._thumb_threads:
            try:
                th.quit()
                th.wait(200)
            except Exception:
                pass
        self._thumb_threads.clear()
        if not templates:
            self._templates_empty.setVisible(True)
            self._templates_layout.addWidget(self._templates_empty)
            return
        self._templates_empty.setVisible(False)
        for tpl in templates:
            card = _TemplateCard(tpl)
            card.removed.connect(self._on_delete_template)
            self._templates_layout.addWidget(card)
            if tpl.url:
                self._spawn_thumbnail_loader(card, tpl)

    def _spawn_thumbnail_loader(self, card: _TemplateCard, tpl: "DetectionTemplate") -> None:
        # Fast path: load from local shared DATA tree when available.
        local_candidates: List[Path] = []
        direct_path = Path((tpl.path or "").strip())
        if direct_path.is_file():
            image = QImage(str(direct_path))
            if not image.isNull():
                card.set_thumbnail(image)
                return
        raw_path = (tpl.path or "").strip().lstrip("/")
        if raw_path:
            local_candidates.append(DATA_DIR / raw_path)
        handler_rel = (tpl.handler_relative or "").strip().lstrip("/")
        if handler_rel:
            local_candidates.append(DATA_DIR / "detection_data" / handler_rel)
        for p in local_candidates:
            if p.exists() and p.is_file():
                img = QImage(str(p))
                if not img.isNull():
                    card.set_thumbnail(img)
                    return

        urls: List[str] = []
        raw_url = (tpl.url or "").strip()
        base = getattr(api_client.client, "_base", None) if api_client.client is not None else None
        if raw_url:
            if raw_url.startswith("/"):
                if base:
                    urls.append(base.rstrip("/") + raw_url)
                urls.append(raw_url)
            else:
                urls.append(raw_url)

        # Fallbacks when serializer URL is missing/malformed.
        raw_path = (tpl.path or "").lstrip("/")
        if raw_path:
            rel_media = f"/media/{raw_path}"
            if base:
                urls.append(base.rstrip("/") + rel_media)
            urls.append(rel_media)
        handler_rel = (tpl.handler_relative or "").lstrip("/")
        if handler_rel:
            rel = f"/media/detection_data/{handler_rel}"
            if base:
                urls.append(base.rstrip("/") + rel)
            urls.append(rel)

        # Deduplicate while preserving order.
        uniq_urls: List[str] = []
        for u in urls:
            if u and u not in uniq_urls:
                uniq_urls.append(u)
        thread = QThread(self)
        loader = _ThumbnailLoader(tpl.id, uniq_urls)
        loader.moveToThread(thread)
        thread.started.connect(loader.run)
        loader.loaded.connect(
            lambda _tid, img, c=card: c.set_thumbnail(img)
        )
        loader.loaded.connect(thread.quit)
        loader.failed.connect(thread.quit)
        thread.finished.connect(loader.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thumb_threads.append(thread)
        thread.start()

    def _on_delete_template(self, template_id: int) -> None:
        if not self._local_mode and not api_client.available:
            return
        if (
            QMessageBox.question(
                self,
                "Delete template",
                "Remove this template file? This can't be undone.",
            )
            != QMessageBox.Yes
        ):
            return
        try:
            local_manager.delete_template(template_id) if self._local_mode else api_client.client.delete_template(template_id)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self._reload_templates()
        self._notify_parent_reload()

    # ----------------------------------------------------------- actions
    def _save(self) -> None:
        if self._profile is None or (not self._local_mode and not api_client.available):
            return
        data = dict(self._profile.data or {})
        data["match_method"] = self.method_combo.currentText()
        data["match_threshold"] = self.threshold_spin.value()
        data["multiple_matches"] = self.multi_check.isChecked()
        try:
            if self._local_mode:
                updated = local_manager.update_profile(
                    self._profile.id,
                    name=self.name_edit.text().strip() or self._profile.name,
                    algorithm=self.algo_combo.currentText(),
                    is_default=self.default_check.isChecked(),
                    data=data,
                )
            else:
                updated = api_client.client.update_profile(
                    self._profile.id,
                    name=self.name_edit.text().strip() or self._profile.name,
                    algorithm=self.algo_combo.currentText(),
                    is_default=self.default_check.isChecked(),
                    data=data,
                )
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._profile = updated
        QMessageBox.information(self, "Saved", f"Profile '{updated.name}' saved.")
        self._notify_parent_reload()

    def _delete(self) -> None:
        if self._profile is None or (not self._local_mode and not api_client.available):
            return
        if (
            QMessageBox.question(
                self,
                "Delete",
                f"Delete profile '{self._profile.name}'?",
            )
            != QMessageBox.Yes
        ):
            return
        try:
            local_manager.delete_profile(self._profile.id) if self._local_mode else api_client.client.delete_profile(self._profile.id)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self._notify_parent_reload()

    def _open_capture(self) -> None:
        camera_id = self.camera_combo.currentText()
        if not camera_id:
            QMessageBox.information(self, "Pick a camera", "Select a camera first.")
            return
        if self._profile is None:
            QMessageBox.information(
                self, "No profile", "Select or create a detection profile first."
            )
            return
        if self._local_mode and (not connection_manager.connected or connection_manager.config.host.strip().lower() not in {"localhost", "127.0.0.1", "::1"}):
            connection_manager.connect("localhost")
        elif not connection_manager.connected:
            QMessageBox.information(self, "Not connected", "Connect to a cnode device first.")
            return
        if self._local_mode:
            local_camera_server.configure_local_camera(camera_id)
        dialog = _TemplateCapture(camera_id, parent=self)
        dialog.exec()
        saved = getattr(dialog, "saved_path", lambda: None)()
        if saved is None:
            return
        if self._local_mode:
            try:
                local_manager.upload_template(self._profile.id, str(saved), name=saved.stem)
            except (OSError, ValueError, KeyError) as exc:
                QMessageBox.warning(self, "Save template failed", str(exc))
                return
            self._reload_templates()
            QMessageBox.information(self, "Template saved", f"Saved <b>{saved.name}</b> to this laptop's local cnode_manager store.")
            self._notify_parent_reload()
            return
        if not api_client.available:
            QMessageBox.warning(
                self,
                "Not connected",
                "Template cropped locally but not uploaded — "
                "cnode_manager is unreachable.",
            )
            return
        try:
            template = api_client.client.upload_template(
                self._profile.id,
                str(saved),
                name=saved.stem,
            )
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Upload failed", str(exc))
            return

        try:
            refreshed = api_client.client.get_profile(self._profile.id)
        except CNodeManagerError as exc:
            log.info("get_profile(%s) after upload failed: %s", self._profile.id, exc)
            refreshed = None
        if refreshed is not None:
            self.set_profile(refreshed)
        else:
            self._reload_templates()

        QMessageBox.information(
            self,
            "Template uploaded",
            f"Uploaded <b>{saved.name}</b> to cnode_manager "
            f"(template id={template.id}).",
        )
        self._notify_parent_reload()

    def _notify_parent_reload(self) -> None:
        parent = self.parent()
        while parent is not None and not isinstance(parent, DetectionProfilesPage):
            parent = parent.parent()
        if isinstance(parent, DetectionProfilesPage):
            parent.reload()


# ---------------------------------------------------------------------------
# Detection Profiles page
# ---------------------------------------------------------------------------

class DetectionProfilesPage(PageBase):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        add_btn = primary_button("  Add Detection Profile", "plus")
        add_btn.clicked.connect(self._on_add)

        header = PageHeader(
            "Detection Profiles",
            right_widget=add_btn,
            icon_name="profiles",
            on_refresh=lambda: self.reload(),
        )
        super().__init__(header, parent)

        # Split layout
        split = QHBoxLayout()
        split.setSpacing(0)
        self.content().addLayout(split)

        left = Card(title="Profiles", icon_name="profiles")
        left.setMinimumWidth(260)
        left.setMaximumWidth(320)

        # Workflow combo now lives in the left panel, above the list.
        wf_row = QHBoxLayout()
        wf_row.setContentsMargins(0, 0, 0, 0)
        wf_row.setSpacing(6)
        wf_label = QLabel("Workflow")
        wf_label.setStyleSheet("font-weight: 600;")
        wf_row.addWidget(wf_label)
        self._workflow_combo = QComboBox()
        self._workflow_combo.setIconSize(QSize(16, 16))
        self._workflow_combo.currentIndexChanged.connect(self._on_workflow_changed)
        wf_row.addWidget(self._workflow_combo, 1)
        left.body().addLayout(wf_row)

        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_select)
        left.add(self.list_widget)
        self.empty_list_label = QLabel()
        self.empty_list_label.setObjectName("Muted")
        self.empty_list_label.setWordWrap(True)
        left.add(self.empty_list_label)
        split.addWidget(left, 0)

        self.details = _ProfileDetails()
        split.addWidget(self.details, 1)

        self.content().addStretch(1)

        # State
        self._workflows: Dict[int, "Workflow"] = {}
        self._profiles: Dict[int, "DetectionProfile"] = {}
        self._local_mode = False

        api_client.availability_changed.connect(lambda _a: self.reload())
        connection_manager.connected_changed.connect(lambda _c: self.reload())
        self.reload()

    # ------------------------------------------------------------- data
    def reload(self) -> None:
        self._local_mode = not api_client.available
        if self._local_mode:
            self.details.set_local_mode(True)
            self._workflows = {wf.id: wf for wf in (local_manager.get_workflow(name) for name in local_manager.list_workflows())}
            self._workflow_combo.blockSignals(True)
            previous = self._workflow_combo.currentData()
            self._workflow_combo.clear()
            for wf in self._workflows.values():
                self._workflow_combo.addItem(icon("workflows"), wf.name, wf.id)
            if previous is not None:
                index = self._workflow_combo.findData(previous)
                if index >= 0:
                    self._workflow_combo.setCurrentIndex(index)
            self._workflow_combo.blockSignals(False)
            self.details.set_cameras(list(local_camera_config.list()))
            self.empty_list_label.setVisible(False)
            self._reload_profiles()
            return
        self.details.set_local_mode(False)
        if not api_client.available:
            self._workflow_combo.blockSignals(True)
            self._workflow_combo.clear()
            self._workflow_combo.blockSignals(False)
            self._profiles.clear()
            self._refresh_list()
            self.empty_list_label.setText(
                "Not connected — connect to a cnode device from the top bar."
            )
            self.empty_list_label.setVisible(True)
            self.details.set_cameras([])
            self.details.set_profile(None)
            return

        # Fetch workflows + cameras
        client = api_client.client
        wf_map: Dict[int, "Workflow"] = {}
        try:
            raw = client.list_workflows()
        except CNodeManagerError as exc:
            log.info("list_workflows failed: %s", exc)
            raw = {}
        for name in raw or {}:
            # ``calibration`` is an internal-only workflow and must not
            # appear in the user-facing selector.
            if name == "calibration":
                continue
            try:
                wf = client.get_workflow(name)
                wf_map[wf.id] = wf
            except CNodeManagerError as exc:
                log.info("get_workflow(%s) failed: %s", name, exc)
        self._workflows = wf_map

        self._workflow_combo.blockSignals(True)
        prev = self._workflow_combo.currentData()
        self._workflow_combo.clear()
        for wf in self._workflows.values():
            self._workflow_combo.addItem(icon("workflows"), wf.name, wf.id)
        if prev and any(prev == wf.id for wf in self._workflows.values()):
            idx = self._workflow_combo.findData(prev)
            if idx >= 0:
                self._workflow_combo.setCurrentIndex(idx)
        self._workflow_combo.blockSignals(False)

        # Camera list from handler config
        camera_ids: List[str] = []
        try:
            cfg = client.get_handler_config()
            camera_ids = list((cfg.get("cameras") or {}).keys())
        except CNodeManagerError as exc:
            log.info("get_handler_config failed: %s", exc)
        self.details.set_cameras(camera_ids)

        self._reload_profiles()

    def _current_workflow_id(self) -> Optional[int]:
        idx = self._workflow_combo.currentIndex()
        if idx < 0:
            return None
        return self._workflow_combo.itemData(idx)

    def _reload_profiles(self) -> None:
        wf_id = self._current_workflow_id()
        if wf_id is None or (not self._local_mode and not api_client.available):
            self._profiles = {}
            self._refresh_list()
            self.empty_list_label.setText(
                "Select a workflow to see its detection profiles."
            )
            self.empty_list_label.setVisible(True)
            self.details.set_profile(None)
            return
        try:
            profiles = local_manager.list_profiles(wf_id) if self._local_mode else api_client.client.list_profiles(workflow_id=wf_id)
        except CNodeManagerError as exc:
            log.info("list_profiles failed: %s", exc)
            profiles = []
        self._profiles = {p.id: p for p in profiles}
        self._refresh_list()
        if self._profiles:
            self.empty_list_label.setVisible(False)
            self.list_widget.setCurrentRow(0)
        else:
            self.empty_list_label.setText(
                "No detection profiles for this workflow yet. Click "
                "<b>Add Detection Profile</b> to create one."
            )
            self.empty_list_label.setVisible(True)
            self.details.set_profile(None)

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for profile in self._profiles.values():
            item = QListWidgetItem(profile.name)
            item.setData(256, profile.id)  # Qt.UserRole
            self.list_widget.addItem(item)

    # ----------------------------------------------------------- events
    def _on_workflow_changed(self, _idx: int) -> None:
        self._reload_profiles()

    def _on_select(self) -> None:
        items = self.list_widget.selectedItems()
        if not items:
            self.details.set_profile(None)
            return
        profile_id = items[0].data(256)
        profile = self._profiles.get(profile_id)
        self.details.set_profile(profile)

    def _on_add(self) -> None:
        if not self._local_mode and not api_client.available:
            log.warning(
                "Add profile: api_client unavailable (connected=%s, active=%s)",
                connection_manager.connected,
                connection_manager.active_device.host if connection_manager.active_device else None,
            )
            QMessageBox.information(
                self, "Not connected", "Connect to a cnode device first."
            )
            return
        wf_id = self._current_workflow_id()
        if wf_id is None:
            QMessageBox.information(
                self, "Pick a workflow", "Select a workflow first."
            )
            return
        name, ok = QInputDialog.getText(self, "New profile", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            profile_args = dict(
                workflow_id=wf_id,
                name=name,
                algorithm="template_matching",
                data={"match_method": "TM_CCOEFF_NORMED", "match_threshold": 0.5, "template_paths": [], "multiple_matches": False},
                is_default=not self._profiles,
            )
            local_manager.create_profile(**profile_args) if self._local_mode else api_client.client.create_profile(**profile_args)
        except CNodeManagerError as exc:
            QMessageBox.warning(self, "Create failed", str(exc))
            return
        self._reload_profiles()
