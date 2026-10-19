"""Send commands to the running cnode_handler over RabbitMQ.

cnode_handler binds a queue on exchange ``moderindustry.handler.commands`` and
consumes JSON commands of the form::

    {"command": "io.set_override", "args": ["value"]}

All RabbitMQ I/O is funnelled through :class:`app.rmq.NODE`. A single worker
thread owns one long-lived ``NODE`` and drains a FIFO of pending commands --
the GUI thread never blocks on the broker.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Iterable, Optional

from PySide6.QtCore import QObject, Signal

from app.rmq import NODE

log = logging.getLogger("Commander")


EXCHANGE_NAME = "moderindustry.handler.commands"
QUEUE_NAME = "moderindustry.handler.commands"

_SHUTDOWN = object()


class Commander(QObject):
    """Publishes command JSON to the cnode_handler command exchange."""

    command_sent = Signal(dict)
    command_failed = Signal(str, str)  # (command, reason)

    def __init__(self, connection_manager) -> None:
        super().__init__()
        self._conn = connection_manager
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._node: Optional[NODE] = None
        self._topology_declared = False

        connection_manager.connected_changed.connect(self._on_connection_changed)

    # --------------------------------------------------------------- lifecycle
    def _on_connection_changed(self, is_connected: bool) -> None:
        if is_connected:
            self._ensure_worker()
        else:
            self._shutdown_worker()

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._worker_run, name="commander", daemon=True
        )
        self._thread.start()

    def _shutdown_worker(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._queue.put(_SHUTDOWN)
        self._thread.join(timeout=2.0)
        self._thread = None
        self._drop_node()

    def _drop_node(self) -> None:
        if self._node is not None:
            try:
                self._node.close()
            except Exception:
                pass
        self._node = None
        self._topology_declared = False

    # --------------------------------------------------------------- worker
    #
    # The worker owns the NODE. It must periodically pump pika's I/O loop
    # (process_data_events) even when no command is pending, otherwise the
    # broker times out the idle connection on its heartbeat deadline and the
    # next publish silently lands on a dead socket. We wake up every
    # IDLE_POLL_S seconds to service that.
    IDLE_POLL_S = 5.0

    def _worker_run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self.IDLE_POLL_S)
            except queue.Empty:
                self._tick_idle()
                continue
            if item is _SHUTDOWN:
                break
            self._process(item)
        self._drop_node()

    def _tick_idle(self) -> None:
        """Service pika heartbeats so the broker doesn't kick us mid-idle."""
        if self._node is None:
            return
        if not self._node.process_events(time_limit=0.0):
            log.info("Idle heartbeat detected a dead connection; dropping node")
            self._drop_node()

    def _ensure_node(self) -> bool:
        dev = self._conn.active_device
        if dev is None:
            return False
        # Poll the I/O loop first: this is what reliably detects a
        # server-initiated close. ``channel.is_open`` on its own can lie.
        if self._node is not None and not self._node.process_events(time_limit=0.0):
            self._drop_node()
        if self._node is not None and self._node.is_open():
            return True
        self._drop_node()
        self._node = NODE(
            host=dev.host,
            port=dev.rabbitmq_port,
            username=dev.rmq_username,
            password=dev.rmq_password,
            vhost=dev.rmq_vhost,
            heartbeat=30,
            blocked_connection_timeout=5,
            socket_timeout=3,
            logger=log,
        )
        if not self._node.is_open():
            return False
        # Declare topology once per connection. Without this, a publish with
        # mandatory=True against an unbound queue would raise UnroutableError
        # and drop the command -- plus the handler's basic_get would fail on
        # a non-existent queue.
        if not self._topology_declared:
            self._node.declare_exchange(EXCHANGE_NAME)
            self._node.declare_queue(QUEUE_NAME)
            self._node.bind_queue(QUEUE_NAME, EXCHANGE_NAME)
            self._topology_declared = True
        return True

    def _process(self, payload: dict) -> None:
        cmd = payload.get("command", "")
        body = json.dumps(payload)
        # Retry once on a transient publish failure. Covers the classic case
        # where the previous connection was cleanly torn down but the local
        # pika state hasn't noticed yet -- the first publish discovers it,
        # drops the node, and the second publish opens a fresh one.
        for attempt in (1, 2):
            if not self._ensure_node():
                if attempt == 2:
                    log.warning("Command %s failed: broker unreachable", cmd)
                    self.command_failed.emit(cmd, "broker unreachable")
                    return
                continue
            assert self._node is not None
            if self._node.send(body, EXCHANGE_NAME, ""):
                log.info("Sent command %s (attempt %d)", payload, attempt)
                self.command_sent.emit(payload)
                return
            self._drop_node()
        log.warning("Command %s send failed after retry", payload)
        self.command_failed.emit(cmd, "publish failed")

    # --------------------------------------------------------------- public
    def send(self, command: str, args: Optional[Iterable[Any]] = None) -> bool:
        if not self._conn.connected or self._conn.active_device is None:
            self.command_failed.emit(command, "not connected")
            log.warning("Cannot send '%s': not connected", command)
            return False

        payload: dict = {"command": command}
        if args is not None:
            payload["args"] = list(args)

        self._ensure_worker()
        self._queue.put(payload)
        return True

    # --------------------------------------------------------------- IO
    def set_override(self, value: bool) -> bool:
        return self.send("io.set_override", [bool(value)])

    def set_input(self, name: str, value: bool) -> bool:
        return self.send("io.set_input", [name, bool(value)])

    def set_output(self, name: str, value: bool) -> bool:
        return self.send("io.set_output", [name, bool(value)])

    # ----------------------------------------------------------- workflow
    def set_workflow(self, name: str) -> bool:
        return self.send("workflow.set", [name])

    def run_workflow(self) -> bool:
        return self.send("workflow.run")

    def stop_workflow(self) -> bool:
        return self.send("workflow.stop")

    def pause_workflow(self) -> bool:
        return self.send("workflow.pause")

    def switch_workflow(self, name: str) -> bool:
        return self.send("workflow.switch", [name])

    # --------------------------------------------------------- calibration
    def calibration_read_wf_config(self) -> bool:
        return self.send("calibration.read_wf_config")

    def calibration_initialize(self) -> bool:
        return self.send("calibration.initialize")

    def calibration_set_camera_profile(self, camera_id: str, profile_name: str) -> bool:
        return self.send(
            "calibration.set_camera_profile", [camera_id, profile_name]
        )
