"""Account manager: in-memory login state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal


@dataclass
class Account:
    username: str
    role: str = "operator"


class AccountManager(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._account: Optional[Account] = None

    @property
    def account(self) -> Optional[Account]:
        return self._account

    @property
    def is_logged_in(self) -> bool:
        return self._account is not None

    def login(self, username: str, password: str, role: str = "operator") -> bool:
        if not username or not password:
            return False
        self._account = Account(username=username, role=role)
        self.changed.emit()
        return True

    def logout(self) -> None:
        self._account = None
        self.changed.emit()
