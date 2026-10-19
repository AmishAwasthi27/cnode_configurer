"""Shared reactive application state."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.constants import (
    ACCENT_MAINTENANCE,
    ACCENT_PRODUCTION,
    MODE_MAINTENANCE,
    MODE_PRODUCTION,
)


class AppState(QObject):
    """Holds global runtime state and broadcasts changes via signals."""

    mode_changed = Signal(str)
    accent_changed = Signal(str)
    sidebar_visibility_override_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._mode = MODE_PRODUCTION
        self._sidebar_visibility_override = False

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def accent(self) -> str:
        return ACCENT_PRODUCTION if self._mode == MODE_PRODUCTION else ACCENT_MAINTENANCE

    @property
    def sidebar_visibility_override(self) -> bool:
        return self._sidebar_visibility_override

    def set_mode(self, mode: str) -> None:
        if mode not in {MODE_PRODUCTION, MODE_MAINTENANCE}:
            raise ValueError(f"Unknown mode: {mode}")
        if mode == self._mode:
            return
        self._mode = mode
        self.mode_changed.emit(mode)
        self.accent_changed.emit(self.accent)

    def toggle_mode(self) -> None:
        self.set_mode(
            MODE_MAINTENANCE if self._mode == MODE_PRODUCTION else MODE_PRODUCTION
        )

    def set_sidebar_visibility_override(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._sidebar_visibility_override:
            return
        self._sidebar_visibility_override = enabled
        self.sidebar_visibility_override_changed.emit(enabled)


app_state = AppState()
