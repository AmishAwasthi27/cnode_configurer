"""Thin Qt-friendly wrapper around ``cnode_manager_sdk.CNodeManager``.

Rebuilds the underlying SDK client whenever the active device changes so
pages can call straightforward typed SDK methods like::

    api.client.list_workflows()
    api.client.create_profile(workflow_id=..., name=..., algorithm=..., data={})

When we are not connected, ``api.client`` is ``None`` and ``api.available`` is
``False``; pages render empty states in that case.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("APIClient")

try:
    from cnode_manager_sdk import CNodeManager, CNodeManagerError
    _HAS_SDK = True
    log.info("cnode_manager_sdk loaded OK")
except ImportError as exc:  # pragma: no cover - SDK should be on sys.path
    CNodeManager = None  # type: ignore[assignment]
    CNodeManagerError = Exception  # type: ignore[assignment]
    _HAS_SDK = False
    log.error(
        "cnode_manager_sdk unavailable: %s — configurer will run in offline mode",
        exc,
    )


class ManagerClient(QObject):
    """Keeps a live :class:`CNodeManager` pointed at the active device."""

    availability_changed = Signal(bool)

    REQUEST_TIMEOUT = 3.0

    def __init__(self, connection_manager) -> None:
        super().__init__()
        self._conn = connection_manager
        self._client: Optional["CNodeManager"] = None
        self._base_url: Optional[str] = None

        connection_manager.connected_changed.connect(self._refresh)
        connection_manager.connection_changed.connect(lambda _cfg: self._refresh())
        self._refresh()

    # -------------------------------------------------------------- state
    @property
    def client(self) -> Optional["CNodeManager"]:
        return self._client

    @property
    def available(self) -> bool:
        return self._client is not None

    # -------------------------------------------------------------- impl
    def _refresh(self, *_args) -> None:
        if not _HAS_SDK:
            return
        if not self._conn.connected or self._conn.active_device is None:
            if self._client is not None:
                log.info("cnode_manager SDK client released")
                self._client = None
                self._base_url = None
                self.availability_changed.emit(False)
            return
        dev = self._conn.active_device
        url = f"http://{dev.host}:{dev.manager_port}"
        if self._client is not None and self._base_url == url:
            return
        self._client = CNodeManager(url, timeout=self.REQUEST_TIMEOUT)
        self._base_url = url
        log.info("cnode_manager SDK pointed at %s (timeout=%.1fs)", url, self.REQUEST_TIMEOUT)
        self.availability_changed.emit(True)

    # -------------------------------------------------------- convenience
    def safe(self, call, default=None):
        """Call a zero-arg callable on the SDK and swallow transport errors."""
        if self._client is None:
            return default
        try:
            return call()
        except CNodeManagerError as exc:
            log.info("SDK call failed: %s", exc)
            return default
