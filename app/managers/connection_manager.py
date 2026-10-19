"""Connection manager.

Owns a list of **saved devices** (each describing a cnode host the user has
connected to at least once) plus an "active" device selection. The service
stores and reloads the list from ``connections.json``.

Nothing touches the network until :meth:`connect` is called. Port probing
runs in a background thread so it cannot stall the UI.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from app.constants import (
    CONNECTIONS_FILE,
    DEFAULT_HOST,
    PORT_CAMERA_STREAM,
    PORT_DETECTION_STREAM,
    PORT_MANAGER_API,
    PORT_RMQ,
    PORT_SSH,
    PORT_SUPERVISOR,
    PROBED_SERVICES,
)

log = logging.getLogger("ConnectionManager")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Device:
    host: str = DEFAULT_HOST
    label: str = ""
    #: Friendly name from handler ``device_name`` (persisted when we read handler config).
    device_name: str = ""
    camera_stream_port: int = PORT_CAMERA_STREAM
    detection_stream_port: int = PORT_DETECTION_STREAM
    supervisor_port: int = PORT_SUPERVISOR
    rabbitmq_port: int = PORT_RMQ
    ssh_port: int = PORT_SSH
    manager_port: int = PORT_MANAGER_API
    rmq_username: str = "cnode"
    rmq_password: str = "cnode"
    rmq_vhost: str = "cnode"
    last_connected_at: str = ""

    @property
    def display_name(self) -> str:
        if self.device_name.strip():
            return self.device_name.strip()
        if self.label.strip():
            return self.label.strip()
        return self.host


# Backwards-compatible alias — the TopBar, MJPEGView, etc. historically
# accessed ``connection_manager.config.<attr>``; keeping the name means those
# callers do not need to change.
ConnectionConfig = Device


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ConnectionManager(QObject):
    """Multi-device connection manager.

    Emits:
        connection_changed(Device)     — active device config changed.
        status_changed(dict)           — {service: "ok"|"bad"|"unknown"}.
        connected_changed(bool)        — user-intent connection state.
        devices_changed(list[Device])  — saved device list changed.
    """

    connection_changed = Signal(object)
    status_changed = Signal(dict)
    connected_changed = Signal(bool)
    devices_changed = Signal(list)
    _probe_result = Signal(dict)

    PROBE_INTERVAL_MS = 5000
    PROBE_TIMEOUT_S = 0.8

    def __init__(self) -> None:
        super().__init__()
        self._devices: List[Device] = []
        self._active_host: Optional[str] = None
        self._statuses: Dict[str, str] = {name: "unknown" for name in PROBED_SERVICES}
        self._connected = False
        self._probe_running = False
        self.load()

        self._timer = QTimer(self)
        self._timer.setInterval(self.PROBE_INTERVAL_MS)
        self._timer.timeout.connect(self._trigger_probe)

        self._probe_result.connect(self._on_probe_result)

    # --------------------------------------------------------------- state
    @property
    def config(self) -> Device:
        dev = self.active_device
        return dev if dev is not None else Device()

    @property
    def active_device(self) -> Optional[Device]:
        if self._active_host is None:
            return None
        return self._find(self._active_host)

    @property
    def statuses(self) -> Dict[str, str]:
        return dict(self._statuses)

    @property
    def connected(self) -> bool:
        return self._connected

    def devices(self) -> List[Device]:
        return list(self._devices)

    # ---------------------------------------------------------- device CRUD
    def _find(self, host: str) -> Optional[Device]:
        host_l = (host or "").strip().lower()
        for dev in self._devices:
            if dev.host.lower() == host_l:
                return dev
        return None

    def add_device(self, host: str, label: str = "", **overrides: Any) -> Device:
        host = (host or "").strip()
        if not host:
            raise ValueError("Host is required.")
        existing = self._find(host)
        if existing is not None:
            if label:
                existing.label = label
            for k, v in overrides.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.save()
            self.devices_changed.emit(self.devices())
            return existing
        dev = Device(host=host, label=label)
        for k, v in overrides.items():
            if hasattr(dev, k):
                setattr(dev, k, v)
        self._devices.append(dev)
        self.save()
        self.devices_changed.emit(self.devices())
        return dev

    def remove_device(self, host: str) -> None:
        existing = self._find(host)
        if existing is None:
            return
        if self._active_host and self._active_host.lower() == host.lower():
            self.disconnect()
        self._devices = [d for d in self._devices if d is not existing]
        self.save()
        self.devices_changed.emit(self.devices())

    def update_device(self, host: str, **fields: Any) -> Optional[Device]:
        dev = self._find(host)
        if dev is None:
            return None
        for k, v in fields.items():
            if hasattr(dev, k):
                setattr(dev, k, v)
        self.save()
        self.devices_changed.emit(self.devices())
        if self._active_host and self._active_host.lower() == dev.host.lower():
            self.connection_changed.emit(dev)
            if self._connected:
                self._trigger_probe()
        return dev

    # ---------------------------------------------------------- public API
    def connect(self, host: Optional[str] = None, label: str = "") -> None:
        """Activate a device and start probing + live streams.

        If ``host`` is given it is added/updated first.
        """
        if host and host.strip():
            self.add_device(host.strip(), label=label)
            self._active_host = host.strip()
        elif self._active_host is None and self._devices:
            self._active_host = self._devices[0].host

        active = self.active_device
        if active is None:
            raise RuntimeError("No device selected to connect to.")

        active.last_connected_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        self.save()
        self.connection_changed.emit(active)

        if self._connected:
            # Host may have changed; refresh probing.
            self._trigger_probe()
            return

        self._connected = True
        self.connected_changed.emit(True)
        self._timer.start()
        self._trigger_probe()
        log.info("Connected to %s", active.host)

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        self._timer.stop()
        self._statuses = {name: "unknown" for name in PROBED_SERVICES}
        self.status_changed.emit(self.statuses)
        self.connected_changed.emit(False)
        log.info("Disconnected")

    def set_active(self, host: str) -> None:
        dev = self._find(host)
        if dev is None:
            return
        self._active_host = dev.host
        self.connection_changed.emit(dev)
        if self._connected:
            self._trigger_probe()

    # Compatibility shim for callers that still use ``update(**fields)``.
    def update(self, **kwargs) -> None:
        if self.active_device is None:
            return
        self.update_device(self.active_device.host, **kwargs)

    def refresh_statuses(self) -> None:
        if self._connected:
            self._trigger_probe()

    # ---------------------------------------------------------- probing
    def _trigger_probe(self) -> None:
        if self._probe_running or self.active_device is None:
            return
        self._probe_running = True
        threading.Thread(
            target=self._probe_worker_run,
            name="connection-probe",
            daemon=True,
        ).start()

    def _probe_worker_run(self) -> None:
        try:
            dev = self.active_device
            if dev is None:
                return
            statuses: Dict[str, str] = {}
            for service, default_port in PROBED_SERVICES.items():
                port = getattr(dev, f"{service}_port", default_port)
                statuses[service] = "ok" if self._probe(dev.host, port) else "bad"
            self._probe_result.emit(statuses)
        finally:
            self._probe_running = False

    def _on_probe_result(self, statuses: Dict[str, str]) -> None:
        if not self._connected:
            return
        if statuses != self._statuses:
            self._statuses = statuses
            self.status_changed.emit(self.statuses)

    @classmethod
    def _probe(cls, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=cls.PROBE_TIMEOUT_S):
                return True
        except OSError:
            return False

    # ---------------------------------------------------------- persistence
    def load(self) -> None:
        if not CONNECTIONS_FILE.exists():
            self._devices = [Device()]
            self._active_host = self._devices[0].host
            self.save()
            return
        try:
            raw = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Failed to load %s: %s", CONNECTIONS_FILE, exc)
            self._devices = [Device()]
            self._active_host = self._devices[0].host
            return

        # New format: {"devices": [...], "active_host": "..."}
        if isinstance(raw, dict) and isinstance(raw.get("devices"), list):
            self._devices = [self._device_from_raw(d) for d in raw["devices"]]
            self._active_host = raw.get("active_host")
        # Old format: a single flat device dict.
        elif isinstance(raw, dict):
            self._devices = [self._device_from_raw(raw)]
            self._active_host = self._devices[0].host
        else:
            self._devices = [Device()]
            self._active_host = self._devices[0].host

        if self._active_host and self._find(self._active_host) is None:
            self._active_host = self._devices[0].host if self._devices else None

    @staticmethod
    def _device_from_raw(raw: Dict[str, Any]) -> Device:
        dev = Device()
        for field_name in (f.name for f in _device_fields()):
            if field_name in raw:
                setattr(dev, field_name, raw[field_name])
        return dev

    def save(self) -> None:
        data = {
            "active_host": self._active_host,
            "devices": [asdict(d) for d in self._devices],
        }
        CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONNECTIONS_FILE.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )


# Helper: list of Device fields without introducing a hard dataclasses import
# at call time. Declared at module load to keep things simple.
def _device_fields():
    from dataclasses import fields as _f

    return _f(Device)


# The module-level singleton continues to live in ``managers/__init__.py``.
