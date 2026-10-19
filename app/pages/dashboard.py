"""Dashboard page: live inputs/outputs + one card per configured camera."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.managers import api_client, connection_manager, status_manager
from app.pages._common import PageBase, PageHeader, card_title
from app.widgets.card import Card
from app.widgets.mjpeg_view import MJPEGView, StreamSource

log = logging.getLogger("DashboardPage")

# Matches cnode_handler when profile omits ``match_threshold`` (see detector / simple_detect).
DEFAULT_MATCH_THRESHOLD = 0.7

try:
    from cnode_manager_sdk import CNodeManagerError
except ImportError:  # pragma: no cover
    CNodeManagerError = Exception  # type: ignore[assignment]


def _camera_id_from_result(res: Dict[str, Any]) -> str:
    """Resolve camera id from streamed detection dict (``camera`` or ``name``)."""
    cam = res.get("camera")
    if isinstance(cam, str) and cam:
        return cam
    name = res.get("name") or ""
    if isinstance(name, str) and "." in name:
        return name.split(".", 1)[0]
    return ""


def _display_template_name(raw: str) -> str:
    """Basename only, strip common image extensions — tags show logical names, not paths."""
    s = str(raw).strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    low = s.lower()
    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"):
        if low.endswith(ext):
            return s[: -len(ext)]
    return s


def _template_names_from_compose_profile(prof: Any) -> List[str]:
    """Template display names from a composed detection profile dict."""
    if not isinstance(prof, dict):
        return []
    names: List[str] = []
    for t in prof.get("templates") or []:
        if not isinstance(t, dict):
            continue
        raw_name = (t.get("name") or "").strip()
        path = t.get("path") or t.get("handler_relative") or ""
        if raw_name:
            disp = _display_template_name(raw_name)
        elif path:
            disp = _display_template_name(str(path))
        else:
            continue
        if disp and disp not in names:
            names.append(disp)
    for p in prof.get("template_paths") or []:
        disp = _display_template_name(str(p))
        if disp and disp not in names:
            names.append(disp)
    return names


def _template_names_from_live_results(results: List[Dict[str, Any]]) -> List[str]:
    """Derive template names from streamed ``name`` fields (``camera.template``)."""
    seen: List[str] = []
    for res in results:
        name = res.get("name") or ""
        if not isinstance(name, str) or "." not in name:
            continue
        tpl = name.split(".", 1)[1].strip()
        disp = _display_template_name(tpl)
        if disp and disp not in seen:
            seen.append(disp)
    return seen


# ---------------------------------------------------------------------------
# IO strip (live inputs / outputs shown in the page header)
# ---------------------------------------------------------------------------

class _IOStrip(QFrame):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.title = title
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._title_label = QLabel(f"{title}:")
        self._title_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(self._title_label)
        self._pills_host = QHBoxLayout()
        self._pills_host.setSpacing(4)
        layout.addLayout(self._pills_host)
        self._pills: Dict[str, QLabel] = {}

    def set_values(self, values: Dict[str, bool]) -> None:
        if set(values.keys()) != set(self._pills.keys()):
            while self._pills_host.count():
                item = self._pills_host.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
            self._pills.clear()
            for name in values:
                pill = QLabel(name)
                pill.setAlignment(Qt.AlignCenter)
                pill.setFixedHeight(22)
                pill.setMinimumWidth(42)
                self._pills[name] = pill
                self._pills_host.addWidget(pill)
        for name, value in values.items():
            pill = self._pills[name]
            color = "#16A34A" if value else "#9CA3AF"
            pill.setStyleSheet(
                f"background: {color}; color: white; border-radius: 10px;"
                f" padding: 2px 8px; font-weight: 700;"
            )


# ---------------------------------------------------------------------------
# Per-camera row (stream + readout)
# ---------------------------------------------------------------------------

def _tag_pill(text: str, *, bg: str = "#E0E7FF", fg: str = "#1E3A8A") -> QLabel:
    """Small colour pill used for profile / value chips on the detection card."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background: {bg}; color: {fg}; padding: 2px 10px; "
        f"border-radius: 10px; font-weight: 600;"
    )
    lbl.setAlignment(Qt.AlignCenter)
    # Avoid shrinking to zero width in tight grid / scroll layouts.
    lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    lbl.setMinimumHeight(26)
    return lbl


def _template_name_chip(text: str) -> QLabel:
    """Tag-style chip for template names (bordered pill)."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "background: #F9FAFB; color: #111827; padding: 4px 12px; "
        "border-radius: 12px; border: 1px solid #D1D5DB; font-weight: 600; "
        "font-size: 11pt;"
    )
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    lbl.setMinimumHeight(28)
    return lbl


class _DetectionCard(QFrame):
    """Summarises the latest detection hits for one camera.

    * Big confidence readout (from last non-empty status, held when status omits hits)
    * Tag row: configured profile + DB template names
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._card = Card()
        self._card.body().addWidget(card_title("detect", "Detection"))

        # Confidence readout (headline figure)
        self._confidence_label = QLabel("—")
        self._confidence_label.setAlignment(Qt.AlignCenter)
        self._confidence_label.setStyleSheet(
            "font-size: 34pt; font-weight: 800; color: #1F2937;"
        )
        self._card.body().addWidget(self._confidence_label)

        self._confidence_line1 = QLabel("No detection yet")
        self._confidence_line1.setAlignment(Qt.AlignCenter)
        self._confidence_line1.setObjectName("Muted")
        self._confidence_line1.setTextFormat(Qt.TextFormat.PlainText)
        self._confidence_line1.setWordWrap(True)
        self._card.body().addWidget(self._confidence_line1)
        self._confidence_line2 = QLabel("")
        self._confidence_line2.setAlignment(Qt.AlignCenter)
        self._confidence_line2.setObjectName("Muted")
        self._confidence_line2.setTextFormat(Qt.TextFormat.PlainText)
        self._confidence_line2.setWordWrap(True)
        self._confidence_line2.setVisible(False)
        self._card.body().addWidget(self._confidence_line2)

        # dt (detection cycle time) on its own row — value shown as a chip.
        dt_row = QHBoxLayout()
        dt_row.setContentsMargins(0, 6, 0, 0)
        dt_row.setSpacing(8)
        dt_key = QLabel("Detection time")
        dt_key.setStyleSheet("font-weight: 700;")
        dt_row.addWidget(dt_key)
        self._dt_host = QHBoxLayout()
        self._dt_host.setSpacing(6)
        dt_row.addLayout(self._dt_host, 1)
        self._card.body().addLayout(dt_row)

        # Live workflow state from status payload.
        wf_state_row = QHBoxLayout()
        wf_state_row.setContentsMargins(0, 6, 0, 0)
        wf_state_row.setSpacing(8)
        wf_state_key = QLabel("State")
        wf_state_key.setStyleSheet("font-weight: 700;")
        wf_state_row.addWidget(wf_state_key)
        self._workflow_state_host = QHBoxLayout()
        self._workflow_state_host.setSpacing(6)
        wf_state_row.addLayout(self._workflow_state_host, 1)
        self._card.body().addLayout(wf_state_row)

        # Profile row (tag)
        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 8, 0, 0)
        profile_row.setSpacing(8)
        profile_key = QLabel("Profile")
        profile_key.setStyleSheet("font-weight: 700;")
        profile_row.addWidget(profile_key)
        self._profile_pill_host = QHBoxLayout()
        self._profile_pill_host.setSpacing(4)
        profile_row.addLayout(self._profile_pill_host, 1)
        self._card.body().addLayout(profile_row)

        # match_threshold for this profile (from manager compose) — for pass/fail coloring.
        thr_row = QHBoxLayout()
        thr_row.setContentsMargins(0, 6, 0, 0)
        thr_row.setSpacing(8)
        thr_key = QLabel("Threshold")
        thr_key.setStyleSheet("font-weight: 700;")
        thr_row.addWidget(thr_key)
        self._threshold_host = QHBoxLayout()
        self._threshold_host.setSpacing(6)
        thr_row.addLayout(self._threshold_host, 1)
        self._card.body().addLayout(thr_row)

        # Template tags: scroll row so many templates stay visible in a narrow column.
        tpl_header = QHBoxLayout()
        tpl_header.setContentsMargins(0, 6, 0, 0)
        tpl_header.setSpacing(8)
        tpl_key = QLabel("Templates")
        tpl_key.setStyleSheet("font-weight: 700;")
        tpl_header.addWidget(tpl_key)
        tpl_header.addStretch(1)
        self._card.body().addLayout(tpl_header)

        self._templates_scroll = QScrollArea()
        self._templates_scroll.setFrameShape(QFrame.NoFrame)
        self._templates_scroll.setWidgetResizable(True)
        self._templates_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._templates_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._templates_scroll.setMinimumHeight(44)
        self._templates_inner = QWidget()
        self._tpl_tags_layout = QHBoxLayout(self._templates_inner)
        self._tpl_tags_layout.setContentsMargins(4, 2, 4, 2)
        self._tpl_tags_layout.setSpacing(6)
        self._tpl_tags_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._templates_scroll.setWidget(self._templates_inner)
        self._card.body().addWidget(self._templates_scroll)

        self._card.body().addStretch(1)

        root.addWidget(self._card)
        self.setMinimumWidth(260)

    def _set_confidence_caption(self, line1: str, line2: str) -> None:
        self._confidence_line1.setText(line1)
        t2 = (line2 or "").strip()
        if t2:
            self._confidence_line2.setText(t2)
            self._confidence_line2.setVisible(True)
        else:
            self._confidence_line2.setText("")
            self._confidence_line2.setVisible(False)

    def _clear_layout(self, layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_view(
        self,
        results: List[Dict[str, Any]],
        *,
        profile_name: str,
        db_template_names: List[str],
        workflow_state: str,
        handler_detection_score: Optional[float] = None,
        match_threshold: Optional[float] = None,
    ) -> None:
        self._clear_layout(self._profile_pill_host)
        self._clear_layout(self._tpl_tags_layout)
        self._clear_layout(self._dt_host)
        self._clear_layout(self._workflow_state_host)
        self._clear_layout(self._threshold_host)

        prof = (profile_name or "").strip()
        if prof:
            self._profile_pill_host.addWidget(
                _tag_pill(prof, bg="#E0E7FF", fg="#1E3A8A")
            )
        else:
            muted = QLabel("—")
            muted.setObjectName("Muted")
            self._profile_pill_host.addWidget(muted)
        self._profile_pill_host.addStretch(1)

        th_display = float(match_threshold) if match_threshold is not None else None
        if th_display is not None:
            self._threshold_host.addWidget(
                _tag_pill(f"{th_display:.2f}", bg="#F1F5F9", fg="#0F172A")
            )
        else:
            thr_m = QLabel("—")
            thr_m.setObjectName("Muted")
            thr_m.setToolTip(
                f"No threshold in compose; pass/fail uses {DEFAULT_MATCH_THRESHOLD:.2f} (handler default)."
            )
            self._threshold_host.addWidget(thr_m)
        self._threshold_host.addStretch(1)

        effective_th = (
            float(match_threshold) if match_threshold is not None else DEFAULT_MATCH_THRESHOLD
        )

        def _headline_color_for_score(score_0_1: float) -> str:
            # Handler pass: average_confidence > threshold — fail (reddish) when not above.
            return "#DC2626" if score_0_1 <= effective_th else "#16A34A"

        tpl_names: List[str] = []
        for n in db_template_names:
            disp = _display_template_name(n)
            if disp and disp not in tpl_names:
                tpl_names.append(disp)
        if not tpl_names and results:
            tpl_names = _template_names_from_live_results(results)

        if tpl_names:
            for n in tpl_names:
                self._tpl_tags_layout.addWidget(_template_name_chip(n))
        else:
            empty_tpl = QLabel("—")
            empty_tpl.setObjectName("Muted")
            self._tpl_tags_layout.addWidget(empty_tpl)
        self._tpl_tags_layout.addStretch(1)
        self._templates_inner.adjustSize()

        wf_state_text = (workflow_state or "").strip()
        if wf_state_text:
            self._workflow_state_host.addWidget(
                _tag_pill(wf_state_text, bg="#FEF3C7", fg="#92400E")
            )
        else:
            muted_ws = QLabel("—")
            muted_ws.setObjectName("Muted")
            self._workflow_state_host.addWidget(muted_ws)
        self._workflow_state_host.addStretch(1)

        def _apply_headline_from_handler(score_f: float) -> None:
            pct = max(0.0, min(1.0, score_f)) * 100.0
            self._confidence_label.setText(f"{pct:.1f}%")
            self._confidence_label.setStyleSheet(
                "font-size: 34pt; font-weight: 800; color: "
                + _headline_color_for_score(score_f)
                + ";"
            )
            th_note = f"{effective_th:.2f}" if match_threshold is not None else f"{DEFAULT_MATCH_THRESHOLD:.2f} (default)"
            self._set_confidence_caption(
                f"Detection score (status) · raw {score_f:.4f}",
                f"pass if > {th_note}",
            )

        def _fill_dt_from_headline(headline: Dict[str, Any]) -> None:
            dt_ms_txt = "—"
            dt_raw = headline.get("dt")
            try:
                if dt_raw is not None:
                    dt_ms_txt = f"{float(dt_raw) * 1000.0:.1f} ms"
            except (TypeError, ValueError):
                pass
            if dt_ms_txt != "—":
                self._dt_host.addWidget(
                    _tag_pill(dt_ms_txt, bg="#ECFDF5", fg="#047857")
                )
            else:
                muted_dt = QLabel("—")
                muted_dt.setObjectName("Muted")
                self._dt_host.addWidget(muted_dt)
            self._dt_host.addStretch(1)

        if handler_detection_score is not None:
            _apply_headline_from_handler(float(handler_detection_score))
            if results:
                def _score(r: Dict[str, Any]) -> float:
                    s = r.get("score")
                    try:
                        return float(s) if s is not None else -1.0
                    except (TypeError, ValueError):
                        return -1.0

                headline = max(results, key=_score)
                _fill_dt_from_headline(headline)
            else:
                muted_dt = QLabel("—")
                muted_dt.setObjectName("Muted")
                self._dt_host.addWidget(muted_dt)
                self._dt_host.addStretch(1)
            return

        if not results:
            self._confidence_label.setText("—")
            self._confidence_label.setStyleSheet(
                "font-size: 34pt; font-weight: 800; color: #9CA3AF;"
            )
            self._set_confidence_caption("No detection yet", "")
            muted_dt = QLabel("—")
            muted_dt.setObjectName("Muted")
            self._dt_host.addWidget(muted_dt)
            self._dt_host.addStretch(1)
            return

        # Pick the headline hit (best score)
        def _score(r: Dict[str, Any]) -> float:
            s = r.get("score")
            try:
                return float(s) if s is not None else -1.0
            except (TypeError, ValueError):
                return -1.0

        headline = max(results, key=_score)
        score = headline.get("score")
        matched = bool(headline.get("matched"))
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        if score_f is not None:
            pct = max(0.0, min(1.0, score_f)) * 100.0
            self._confidence_label.setText(f"{pct:.1f}%")
        else:
            self._confidence_label.setText("—")
        if score_f is not None:
            self._confidence_label.setStyleSheet(
                "font-size: 34pt; font-weight: 800; color: "
                + _headline_color_for_score(score_f)
                + ";"
            )
        else:
            self._confidence_label.setStyleSheet(
                "font-size: 34pt; font-weight: 800; color: #9CA3AF;"
            )
        raw_txt = f"{score_f:.4f}" if score_f is not None else "—"
        th_note = (
            f"{effective_th:.2f}" if match_threshold is not None else f"{DEFAULT_MATCH_THRESHOLD:.2f} (default)"
        )
        self._set_confidence_caption(
            f"{'Matched' if matched else 'No match'} · raw score {raw_txt}",
            f"pass if > {th_note}",
        )

        _fill_dt_from_headline(headline)


class _CameraRow(QFrame):
    """Two live streams (camera + detection) side by side, with a detection card."""

    def __init__(self, camera_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.camera_id = camera_id
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        # Camera (direct) stream
        camera_card = Card()
        camera_card.body().addWidget(
            card_title("cameras", f"Camera · {camera_id}")
        )
        self.camera_view = MJPEGView()
        self.camera_view.setMinimumHeight(360)
        camera_card.add(self.camera_view)

        # Detection stream (post-processed)
        detect_card = Card()
        detect_card.body().addWidget(
            card_title("detect", f"Detection feed · {camera_id}")
        )
        self.detect_view = MJPEGView()
        self.detect_view.setMinimumHeight(360)
        detect_card.add(self.detect_view)

        # Detection summary (rich)
        self.results_card = _DetectionCard()
        self.results_card.setMinimumWidth(260)

        grid.addWidget(camera_card, 0, 0, 1, 1)
        grid.addWidget(detect_card, 0, 1, 1, 1)
        grid.addWidget(self.results_card, 0, 2, 1, 1)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 1)

    def set_sources(
        self,
        *,
        camera: Optional[StreamSource],
        detection: Optional[StreamSource],
    ) -> None:
        self.camera_view.set_source(camera)
        self.detect_view.set_source(detection)

    def stop(self) -> None:
        self.camera_view.set_source(None)
        self.detect_view.set_source(None)

    def set_detection_view(
        self,
        results: List[dict],
        *,
        profile_name: str,
        db_template_names: List[str],
        workflow_state: str,
        handler_detection_score: Optional[float] = None,
        match_threshold: Optional[float] = None,
    ) -> None:
        self.results_card.set_view(
            results,
            profile_name=profile_name,
            db_template_names=db_template_names,
            workflow_state=workflow_state,
            handler_detection_score=handler_detection_score,
            match_threshold=match_threshold,
        )


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------

class DashboardPage(PageBase):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self.inputs_strip = _IOStrip("Live Inputs")
        self.outputs_strip = _IOStrip("Live Outputs")

        header_widget = QFrame()
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(24)
        h_layout.addWidget(self.inputs_strip)
        h_layout.addStretch(1)
        h_layout.addWidget(self.outputs_strip)

        header = PageHeader(
            "Dashboard",
            right_widget=header_widget,
            icon_name="dashboard",
            on_refresh=lambda: self.reload(),
        )
        super().__init__(header, parent)

        # Host widget that holds one _CameraRow per configured camera.
        self._camera_host = QWidget()
        self._camera_host_layout = QVBoxLayout(self._camera_host)
        self._camera_host_layout.setContentsMargins(0, 0, 0, 0)
        self._camera_host_layout.setSpacing(0)
        self.content().addWidget(self._camera_host)

        self.empty_state = QFrame()
        empty_root = QVBoxLayout(self.empty_state)
        empty_root.setContentsMargins(20, 20, 20, 20)
        empty_root.setSpacing(10)
        empty_root.addStretch(1)

        self.empty_title = QLabel("No device connected")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size: 20pt; font-weight: 800;")
        empty_root.addWidget(self.empty_title)

        self.empty_label = QLabel(
            "Connect to a cnode device to view live camera streams and detection data."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setContentsMargins(0, 0, 0, 8)
        empty_root.addWidget(self.empty_label)

        self.empty_connect_btn = QPushButton("  Connect device…")
        self.empty_connect_btn.setProperty("accent", True)
        self.empty_connect_btn.setMinimumHeight(36)
        self.empty_connect_btn.clicked.connect(self._open_connection_dialog)
        empty_root.addWidget(self.empty_connect_btn, 0, Qt.AlignHCenter)
        empty_root.addStretch(1)

        self.content().addWidget(self.empty_state)
        self.content().addStretch(1)

        self._rows: Dict[str, _CameraRow] = {}
        # Last non-empty ``detection_result`` items per camera (status may omit hits).
        self._last_detection_by_cam: Dict[str, List[Dict[str, Any]]] = {}
        # Per-camera pipeline scores from status ``detection_scores`` (may persist last value).
        self._last_scores_by_cam: Dict[str, float] = {}
        # (workflow_name, profile_name) -> template display names from manager compose.
        self._compose_tpl_cache: Dict[Tuple[str, str], List[str]] = {}
        # Same key -> raw detection profile dict from workflow compose (or None).
        # Cleared in ``reload()`` and each status tick so profile metadata stays live.
        self._profile_compose_cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        # Per page lifetime: one cnode_manager get_workflow() per workflow name until cleared.
        self._wf_compose_fetch: Dict[str, Any] = {}

        status_manager.status_received.connect(self._on_status)
        connection_manager.connection_changed.connect(lambda _c: self.reload())
        connection_manager.connected_changed.connect(lambda _c: self.reload())
        api_client.availability_changed.connect(lambda _a: self.reload())

    # ---------------------------------------------------------- reload
    def reload(self) -> None:
        """Refresh camera rows from the handler config, then restart streams."""
        self._stop_streams()
        camera_ids: List[str] = []
        if api_client.available:
            try:
                cfg = api_client.client.read_cnode_handler_json()
                camera_ids = list((cfg.get("cameras") or {}).keys())
            except CNodeManagerError as exc:
                log.info("read_cnode_handler_json failed: %s", exc)
        log.info("Dashboard cameras: %s", camera_ids)
        self._last_detection_by_cam.clear()
        self._last_scores_by_cam.clear()
        self._compose_tpl_cache.clear()
        self._profile_compose_cache.clear()
        self._wf_compose_fetch.clear()
        self._rebuild_rows(camera_ids)
        self.empty_state.setVisible(not connection_manager.connected)
        if connection_manager.connected and self.isVisible():
            self._start_streams()

    def _rebuild_rows(self, camera_ids: List[str]) -> None:
        wanted = list(camera_ids)

        # Remove rows that are no longer present.
        for cam_id in list(self._rows):
            if cam_id not in wanted:
                row = self._rows.pop(cam_id)
                self._camera_host_layout.removeWidget(row)
                row.deleteLater()

        # Ensure a row exists for every wanted camera, in order.
        for cam_id in wanted:
            if cam_id not in self._rows:
                row = _CameraRow(cam_id)
                self._rows[cam_id] = row
                self._camera_host_layout.addWidget(row)

    # ------------------------------------------------------------ streams
    def _start_streams(self) -> None:
        if not self._rows:
            return
        cfg = connection_manager.config
        for cam_id, row in self._rows.items():
            row.set_sources(
                camera=StreamSource(
                    host=cfg.host,
                    port=cfg.camera_stream_port,
                    path=f"camera/{cam_id}",
                ),
                detection=StreamSource(
                    host=cfg.host,
                    port=cfg.detection_stream_port,
                    path=f"detection/{cam_id}",
                ),
            )

    def _stop_streams(self) -> None:
        for row in self._rows.values():
            row.stop()

    def _get_workflow_object_for_compose(self, workflow_name: str) -> Any:
        """Load workflow from cnode_manager (cached until compose caches are cleared)."""
        wf = (workflow_name or "").strip()
        if not wf or not api_client.available:
            return None
        key = wf.lower()
        if key in self._wf_compose_fetch:
            return self._wf_compose_fetch[key]
        client = api_client.client
        if client is None:
            self._wf_compose_fetch[key] = None
            return None
        wfo: Any = None
        try:
            wfo = client.get_workflow(wf)
        except CNodeManagerError:
            resolved: Optional[str] = None
            try:
                for cand in (client.list_workflows() or {}).keys():
                    if str(cand).lower() == wf.lower():
                        resolved = str(cand)
                        break
            except CNodeManagerError:
                resolved = None
            if not resolved:
                log.info("get_workflow(%s) for dashboard compose: not found", wf)
                self._wf_compose_fetch[key] = None
                return None
            try:
                wfo = client.get_workflow(resolved)
            except CNodeManagerError as exc:
                log.info("get_workflow(%s) for dashboard compose: %s", resolved, exc)
                self._wf_compose_fetch[key] = None
                return None
        self._wf_compose_fetch[key] = wfo
        return wfo

    def _get_compose_profile_dict(
        self, workflow_name: str, profile_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return the composed ``detection_profiles[profile]`` entry from cnode_manager."""
        wf = (workflow_name or "").strip()
        prof = (profile_name or "").strip()
        if not wf or not prof or not api_client.available:
            return None
        key = (wf, prof)
        if key in self._profile_compose_cache:
            return self._profile_compose_cache[key]
        wfo = self._get_workflow_object_for_compose(wf)
        if wfo is None:
            self._profile_compose_cache[key] = None
            return None
        profiles = (wfo.compose or {}).get("detection_profiles") or {}
        raw: Any = profiles.get(prof)
        if raw is None and prof:
            for k, v in profiles.items():
                if str(k).lower() == prof.lower():
                    raw = v
                    break
        out: Optional[Dict[str, Any]] = raw if isinstance(raw, dict) else None
        # Maintenance runs the reserved ``calibration`` workflow, which borrows
        # cameras/profiles from ``calibration_workflow`` in workflows.json. The
        # manager compose (thresholds, templates) usually lives on that target
        # workflow, not on ``calibration`` itself.
        if out is None and (wf or "").lower() == "calibration" and prof:
            data = wfo.data or {}
            target = (data.get("calibration_workflow") or "").strip()
            if target and target.lower() != wf.lower():
                out = self._get_compose_profile_dict(target, prof)
        self._profile_compose_cache[key] = out
        return out

    def _match_threshold_for_profile(
        self, workflow_name: str, profile_name: str
    ) -> Optional[float]:
        """``match_threshold`` for this profile, if present in manager compose data."""
        raw = self._get_compose_profile_dict(workflow_name, profile_name)
        if raw is None:
            return None
        v = raw.get("match_threshold")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _template_names_for_profile(self, workflow_name: str, profile_name: str) -> List[str]:
        wf = (workflow_name or "").strip()
        prof = (profile_name or "").strip()
        if not wf or not prof or not api_client.available:
            return []
        key = (wf, prof)
        if key in self._compose_tpl_cache:
            return list(self._compose_tpl_cache[key])
        client = api_client.client
        raw = self._get_compose_profile_dict(wf, prof)
        if raw is None:
            self._compose_tpl_cache[key] = []
            return []
        names = _template_names_from_compose_profile(raw)
        if not names and isinstance(raw, dict) and client is not None:
            pid_raw = raw.get("id")
            pid_int: Optional[int] = None
            if isinstance(pid_raw, int):
                pid_int = pid_raw
            elif isinstance(pid_raw, str) and pid_raw.isdigit():
                pid_int = int(pid_raw)
            if pid_int is not None:
                try:
                    for tpl in client.list_templates(profile_id=pid_int):
                        disp = _display_template_name(tpl.name or tpl.path)
                        if disp and disp not in names:
                            names.append(disp)
                except CNodeManagerError as exc:
                    log.info(
                        "list_templates(profile=%s) for dashboard: %s", pid_int, exc
                    )
        self._compose_tpl_cache[key] = names
        return list(names)

    def refresh_compose_caches(self) -> None:
        """Re-fetch profile compose from cnode_manager (threshold, template names) and refresh UI.

        Useful after manual edits; normal status ticks also refresh these caches.
        """
        self._profile_compose_cache.clear()
        self._compose_tpl_cache.clear()
        self._wf_compose_fetch.clear()
        if self._rows and status_manager.last_status:
            self._on_status(status_manager.last_status)

    # ------------------------------------------------------------ signals
    def _on_status(self, payload: dict) -> None:
        self.inputs_strip.set_values(dict(payload.get("input_states") or {}))
        self.outputs_strip.set_values(dict(payload.get("output_states") or {}))
        raw = payload.get("detection_result")
        detection_results: List[dict] = raw if isinstance(raw, list) else []
        current_wf = str(payload.get("current_workflow") or "")
        # Refresh profile metadata (threshold/templates) on every status update.
        self._profile_compose_cache.clear()
        self._compose_tpl_cache.clear()
        self._wf_compose_fetch.clear()
        wf_state_raw = payload.get("workflow_state")
        if isinstance(wf_state_raw, str):
            workflow_state = wf_state_raw
        elif isinstance(wf_state_raw, dict) and wf_state_raw:
            workflow_state = str(
                wf_state_raw.get("state")
                or wf_state_raw.get("name")
                or next(iter(wf_state_raw.values()), "")
            )
        else:
            workflow_state = str(wf_state_raw or "")

        ds_raw = payload.get("detection_scores")
        if isinstance(ds_raw, dict):
            for cam_key, val in ds_raw.items():
                if not isinstance(cam_key, str):
                    continue
                try:
                    self._last_scores_by_cam[cam_key] = float(val)
                except (TypeError, ValueError):
                    pass
        camera_profiles: Dict[str, Any] = dict(payload.get("camera_profiles") or {})

        grouped: Dict[str, List[dict]] = {cam_id: [] for cam_id in self._rows}
        fallback = list(self._rows.keys())[:1]
        for res in detection_results:
            if not isinstance(res, dict):
                continue
            cam = _camera_id_from_result(res)
            if cam and cam in grouped:
                grouped[cam].append(res)
            elif fallback:
                grouped[fallback[0]].append(res)

        for cam_id, row in self._rows.items():
            live = grouped.get(cam_id, [])
            if live:
                self._last_detection_by_cam[cam_id] = [dict(r) for r in live]
            merged = live or self._last_detection_by_cam.get(cam_id, [])

            cam_cfg = camera_profiles.get(cam_id) or {}
            if not isinstance(cam_cfg, dict):
                cam_cfg = {}
            profile_name = str(cam_cfg.get("detection_profile") or "")
            db_names = self._template_names_for_profile(current_wf, profile_name)
            handler_score: Optional[float] = self._last_scores_by_cam.get(cam_id)
            match_th = self._match_threshold_for_profile(current_wf, profile_name)
            row.set_detection_view(
                merged,
                profile_name=profile_name,
                db_template_names=db_names,
                workflow_state=workflow_state,
                handler_detection_score=handler_score,
                match_threshold=match_th,
            )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if connection_manager.connected:
            if not self._rows:
                self.reload()
            self._start_streams()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._stop_streams()

    def _open_connection_dialog(self) -> None:
        mw = self.window()
        if mw is None:
            return
        open_conn = getattr(mw, "_open_connection", None)
        if callable(open_conn):
            open_conn()
