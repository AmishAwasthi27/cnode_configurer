"""Frameless floating panel anchored under a widget (hover to show)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QVBoxLayout,
    QWidget,
)

# Space around the inner panel so the drop shadow is not clipped by the
# top-level window bounds. Should be roughly half the blur radius or more.
_FLYOUT_SHADOW_MARGIN = 18


class AnchorFlyout(QWidget):
    """Tool window that opens under ``anchor`` when the pointer enters it.

    The actual window is a transparent shell with outer margins; the visible
    card (``#FlyoutPanel``) is an inner ``QFrame`` that carries
    ``QGraphicsDropShadowEffect``, so the shadow renders in the margin gutter.
    """

    about_to_show = Signal()

    def __init__(
        self,
        parent: QWidget,
        anchor: QWidget,
        *,
        min_width: int = 360,
        max_height: int = 440,
        hide_delay_ms: int = 320,
        show_without_activating: bool = True,
    ) -> None:
        super().__init__(parent)
        self._anchor = anchor
        self._min_w = min_width
        self._max_h = max_height
        self._hide_delay_ms = hide_delay_ms
        self._show_without_activating = show_without_activating

        self.setObjectName("AnchorFlyoutShell")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WA_ShowWithoutActivating, show_without_activating
        )
        self.setStyleSheet("#AnchorFlyoutShell { background: transparent; }")

        shell = QVBoxLayout(self)
        m = _FLYOUT_SHADOW_MARGIN
        shell.setContentsMargins(m, m, m, m)
        shell.setSpacing(0)

        self._panel = QFrame(self)
        self._panel.setObjectName("FlyoutPanel")

        shadow = QGraphicsDropShadowEffect(self._panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(15, 23, 42, 52))
        self._panel.setGraphicsEffect(shadow)

        self._root = QVBoxLayout(self._panel)
        self._root.setContentsMargins(10, 10, 10, 10)
        self._root.setSpacing(6)

        shell.addWidget(self._panel)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_if_pointer_left)

        anchor.installEventFilter(self)
        self.setMouseTracking(True)

    def body(self) -> QVBoxLayout:
        return self._root

    def set_title(self, text: str) -> None:
        """Optional small title row above injected content."""
        title = QLabel(text)
        title.setStyleSheet("font-weight: 700; font-size: 11pt;")
        self._root.insertWidget(0, title)

    def set_content(self, widget: QWidget) -> None:
        """Append the main body widget (caller owns layout above title if any)."""
        self._root.addWidget(widget, 1)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._anchor:
            et = event.type()
            if et == QEvent.Type.Enter:
                self._hide_timer.stop()
                self._reposition_and_show()
            elif et == QEvent.Type.Leave:
                self._schedule_hide_check()
        return False

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._schedule_hide_check()
        super().leaveEvent(event)

    def _schedule_hide_check(self) -> None:
        self._hide_timer.start(self._hide_delay_ms)

    def _hide_if_pointer_left(self) -> None:
        pos = QCursor.pos()
        if self._anchor_under_pointer(pos):
            return
        if self.frameGeometry().contains(pos):
            return
        self.hide()

    def _anchor_under_pointer(self, global_pos: QPoint) -> bool:
        top_left = self._anchor.mapToGlobal(QPoint(0, 0))
        r = QRect(top_left, self._anchor.size())
        return r.contains(global_pos)

    def _reposition_and_show(self) -> None:
        self.adjustSize()
        w = max(self._min_w, self.sizeHint().width(), self.width())
        h = min(self._max_h, max(self.sizeHint().height(), self.height()))
        self.resize(w, h)

        anchor_tl = self._anchor.mapToGlobal(QPoint(0, 0))
        anchor_br = self._anchor.mapToGlobal(
            QPoint(self._anchor.width(), self._anchor.height())
        )
        # Slight vertical overlap so the cursor path from the anchor into the
        # panel never crosses "dead" space that would arm the hide timer.
        x = anchor_br.x() - self.width()
        y = anchor_br.y() - 2

        screen = QApplication.screenAt(anchor_tl)
        if screen is not None:
            geo = screen.availableGeometry()
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
            y = max(geo.top() + 8, min(y, geo.bottom() - self.height() - 8))

        self.move(x, y)
        self.about_to_show.emit()
        self.show()
        self.raise_()

    def show_for_anchor_click(self) -> None:
        """Open from a click (e.g. keyboard activation); may take focus."""
        self._hide_timer.stop()
        self._reposition_and_show()
        if not self._show_without_activating:
            self.activateWindow()
