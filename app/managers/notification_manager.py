"""Notification manager."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from PySide6.QtCore import QObject, Signal


@dataclass
class Notification:
    title: str
    message: str
    level: str = "info"  # info | warning | error
    read: bool = False
    timestamp: datetime = field(default_factory=datetime.now)


class NotificationManager(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._items: List[Notification] = []

    def add(self, title: str, message: str, level: str = "info") -> None:
        self._items.insert(0, Notification(title=title, message=message, level=level))
        self.changed.emit()

    def mark_all_read(self) -> None:
        for item in self._items:
            item.read = True
        self.changed.emit()

    def clear(self) -> None:
        self._items.clear()
        self.changed.emit()

    @property
    def items(self) -> List[Notification]:
        return list(self._items)

    @property
    def unread_count(self) -> int:
        return sum(1 for n in self._items if not n.read)
