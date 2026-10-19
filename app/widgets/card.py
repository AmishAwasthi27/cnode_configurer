"""Simple rectangular card container."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.icons import pixmap


class Card(QFrame):
    def __init__(
        self,
        parent: QWidget | None = None,
        title: Optional[str] = None,
        icon_name: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(10)

        if title:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            if icon_name:
                icon_label = QLabel()
                icon_label.setPixmap(pixmap(icon_name, color="#1F2937", size=18))
                icon_label.setFixedSize(20, 20)
                row.addWidget(icon_label)
            lbl = QLabel(title)
            lbl.setStyleSheet("font-weight: 700; font-size: 11pt;")
            row.addWidget(lbl)
            row.addStretch(1)
            self._layout.addLayout(row)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)
