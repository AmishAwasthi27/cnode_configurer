"""Shared page building blocks."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.icons import icon, pixmap


class PageHeader(QFrame):
    """Frozen page header: icon + title on the left, optional refresh and a
    caller-provided action widget on the right.
    """

    def __init__(
        self,
        left_title: str,
        right_widget: Optional[QWidget] = None,
        icon_name: Optional[str] = None,
        on_refresh: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        self.setFixedHeight(64)

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(10)

        self._icon_label: Optional[QLabel] = None
        if icon_name:
            self._icon_label = QLabel()
            self._icon_label.setPixmap(pixmap(icon_name, color="#1F2937", size=22))
            self._icon_label.setFixedSize(24, 24)
            row.addWidget(self._icon_label)

        self.title = QLabel(left_title)
        self.title.setObjectName("PageTitle")
        row.addWidget(self.title)
        row.addStretch(1)

        if on_refresh is not None:
            refresh_btn = QPushButton("  Refresh")
            refresh_btn.setIcon(icon("refresh"))
            refresh_btn.setIconSize(QSize(16, 16))
            refresh_btn.setToolTip("Reload this page from cnode_manager")
            refresh_btn.clicked.connect(lambda _=False: on_refresh())
            row.addWidget(refresh_btn)

        if right_widget is not None:
            row.addWidget(right_widget)

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def set_icon(self, icon_name: Optional[str]) -> None:
        if self._icon_label is None:
            return
        if icon_name is None:
            self._icon_label.setVisible(False)
            return
        self._icon_label.setPixmap(pixmap(icon_name, color="#1F2937", size=22))
        self._icon_label.setVisible(True)


class PageBase(QWidget):
    """Page with frozen top header and scrollable content area."""

    def __init__(self, header: PageHeader, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._content = QWidget()
        self._content.setObjectName("centralHost")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

    def content(self) -> QVBoxLayout:
        return self._content_layout


def primary_button(text: str, icon_name: Optional[str] = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setProperty("accent", True)
    if icon_name:
        btn.setIcon(icon(icon_name, color="#FFFFFF"))
        btn.setIconSize(QSize(16, 16))
    return btn


def secondary_button(text: str, icon_name: Optional[str] = None) -> QPushButton:
    btn = QPushButton(text)
    if icon_name:
        btn.setIcon(icon(icon_name))
        btn.setIconSize(QSize(16, 16))
    return btn


def card_title(icon_name: str, text: str) -> QWidget:
    """Return a horizontal icon + bold title row for use inside a Card."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    icon_label = QLabel()
    icon_label.setPixmap(pixmap(icon_name, color="#1F2937", size=18))
    icon_label.setFixedSize(20, 20)
    title = QLabel(text)
    title.setStyleSheet("font-weight: 700; font-size: 11pt;")
    layout.addWidget(icon_label)
    layout.addWidget(title)
    layout.addStretch(1)
    return row
