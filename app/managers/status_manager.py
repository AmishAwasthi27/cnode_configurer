"""Status manager: background RabbitMQ consumer for handler status events.

Consumes ``moderindustry.status`` messages published by ``cnode_handler`` via
:class:`app.rmq.NODE` (the shared pika wrapper) and re-emits them as Qt
signals. If the broker is unreachable the thread just reconnects on a loop;
it never raises into the UI.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from app.rmq import NODE

log = logging.getLogger("StatusManager")


class StatusManager(QObject):
    status_received = Signal(dict)
    connected = Signal(bool)

    QUEUE_NAME = "moderindustry.status"
    EXCHANGE_NAME = "moderindustry.status"
    POLL_IDLE_S = 0.2
    RECONNECT_DELAY_S = 3.0

    def __init__(self, connection_manager) -> None:
        super().__init__()
        self._conn = connection_manager
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_status: Dict[str, Any] = {}

    @property
    def last_status(self) -> Dict[str, Any]:
        return dict(self._last_status)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="status-consumer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------ loop
    def _run(self) -> None:
        while not self._stop.is_set():
            dev = self._conn.active_device
            if dev is None:
                time.sleep(self.RECONNECT_DELAY_S)
                continue

            node = NODE(
                host=dev.host,
                port=dev.rabbitmq_port,
                username=dev.rmq_username,
                password=dev.rmq_password,
                vhost=dev.rmq_vhost,
                heartbeat=30,
                blocked_connection_timeout=10,
                socket_timeout=3,
                logger=log,
            )
            if not node.is_open():
                self.connected.emit(False)
                node.close()
                if self._stop.wait(self.RECONNECT_DELAY_S):
                    return
                continue

            # node.declare_exchange(self.EXCHANGE_NAME)
            # node.declare_queue(self.QUEUE_NAME)
            # node.bind_queue(self.QUEUE_NAME, self.EXCHANGE_NAME)
            self.connected.emit(True)
            log.info(
                "Status consumer connected to %s:%s", dev.host, dev.rabbitmq_port
            )

            try:
                self._consume_loop(node)
            except Exception as exc:  # pragma: no cover - networking
                log.info("Status consumer error: %s", exc)
            finally:
                node.close()
                self.connected.emit(False)

            if self._stop.is_set():
                return
            time.sleep(self.RECONNECT_DELAY_S)

    def _consume_loop(self, node: NODE) -> None:
        while not self._stop.is_set() and node.is_open():
            body = node.recv(self.QUEUE_NAME)
            if not body or body == "{}":
                # Service heartbeats during the idle wait so the broker
                # doesn't close the connection while we sleep.
                if not node.process_events(time_limit=self.POLL_IDLE_S):
                    return
                if self._stop.is_set():
                    return
                continue
            try:
                payload = json.loads(body)
            except ValueError:
                continue
            self._last_status = payload
            self.status_received.emit(payload)
