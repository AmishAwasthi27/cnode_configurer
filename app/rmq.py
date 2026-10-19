"""RabbitMQ node wrapper used everywhere in cnode_configurer.

Same shape as ``cnode_handler/rmq.py`` (the handler-side copy), but the
``__init__`` takes explicit connection parameters instead of reading a
global ``config`` module so the configurer can point it at whichever
``Device`` the user has selected.

All RabbitMQ I/O in the configurer -- command publishing (Commander) and
status consumption (StatusManager) -- goes through this class.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import pika
from pika.exceptions import AMQPConnectionError

logging.getLogger("pika").setLevel(logging.CRITICAL)


class NODE:
    """Thin pika wrapper: one persistent BlockingConnection + channel.

    Not thread-safe -- create a separate NODE per thread (pika requirement).
    """

    conn = False
    queue_name = ""
    exchange = ""
    key = ""
    vhost = ""

    def __init__(
        self,
        *,
        host: str,
        port: int = 5672,
        username: str = "cnode",
        password: str = "cnode",
        vhost: str = "cnode",
        max_queue: int = 10,
        heartbeat: int = 30,
        blocked_connection_timeout: int = 10,
        socket_timeout: float = 5.0,
        connection_attempts: int = 1,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.vhost = vhost
        self.max_queue = max_queue
        self.max_len = max_queue
        self.heartbeat = heartbeat
        self.blocked_connection_timeout = blocked_connection_timeout
        self.socket_timeout = socket_timeout
        self.connection_attempts = connection_attempts

        self.message = ""
        self.RECIEVE = False
        self.consume_trigger = False
        self.consume_message = False
        self.init_c = 0
        self.queue_name = ""
        self.exchange = ""
        self.key = ""
        self.channel = None
        self.connection = None
        self.credentials = None
        self.conn = None

        self.logger = logger or logging.getLogger("RMQ")

        self.connect()

    # ------------------------------------------------------------------ setup
    def _params(self) -> pika.ConnectionParameters:
        self.credentials = pika.PlainCredentials(self.username, self.password)
        return pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            credentials=self.credentials,
            virtual_host=self.vhost,
            heartbeat=self.heartbeat,
            blocked_connection_timeout=self.blocked_connection_timeout,
            socket_timeout=self.socket_timeout,
            connection_attempts=self.connection_attempts,
        )

    def connect(self) -> bool:
        try:
            self.connection = pika.BlockingConnection(self._params())
            self.channel = self.connection.channel()
            if self.channel is None:
                self.logger.error("Channel is None")
                raise Exception("No Channel Created!")
            # Publisher confirms: basic_publish blocks until the broker acks,
            # so we can't lose a command on a half-dead connection.
            try:
                self.channel.confirm_delivery()
            except Exception as err:  # pragma: no cover - broker variance
                self.logger.warning("confirm_delivery() not enabled: %s", err)
            self.conn = self.channel.is_open
            self.logger.info(
                "Connected MQ at %s:%s as %s", self.host, self.port, self.username
            )
            return True
        except (AMQPConnectionError, ConnectionRefusedError) as err:
            self.logger.info("RabbitMQ connection refused at %s: %s", self.host, err)
            self.conn = False
            self.channel = None
            self.connection = None
            return False
        except Exception as err:
            self.logger.warning("Error connecting RabbitMQ: %s", err)
            self.conn = False
            self.channel = None
            self.connection = None
            return False

    def process_events(self, time_limit: float = 0.0) -> bool:
        """Pump pika's I/O loop once so heartbeats are serviced.

        Returns False if the connection is now closed.
        """
        try:
            if self.connection is None or not self.connection.is_open:
                return False
            self.connection.process_data_events(time_limit=time_limit)
            return self.is_open()
        except Exception as err:
            self.logger.info("process_data_events failed: %s", err)
            return False

    def is_open(self) -> bool:
        try:
            return (
                self.channel is not None
                and self.channel.is_open
                and self.connection is not None
                and self.connection.is_open
            )
        except Exception:
            return False

    def close(self) -> None:
        try:
            if self.connection is not None and self.connection.is_open:
                self.connection.close()
        except Exception:
            pass
        finally:
            self.channel = None
            self.connection = None
            self.conn = False

    # -------------------------------------------------------------- topology
    def declare_exchange(self, name: str, type: str = "direct") -> None:
        try:
            self.channel.exchange_declare(
                exchange=name, exchange_type=type, durable=True
            )
        except Exception as err:
            self.logger.error("Error declaring exchange %s: %s", name, err)

    def declare_queue(self, name: str) -> None:
        try:
            self.channel.queue_declare(
                queue=name,
                auto_delete=False,
                arguments={"x-max-length": self.max_len},
                durable=True,
            )
            self.channel.basic_qos(prefetch_count=1)
        except Exception as err:
            self.logger.error("Error declaring queue %s: %s", name, err)

    def bind_queue(
        self,
        queue_name: str,
        exchange_name: str = "",
        routing_key: str = "",
    ) -> None:
        # pika's default ``routing_key`` for ``queue_bind`` is the queue
        # *name* (not an empty string). cnode_handler publishes with
        # ``routing_key=""``, so we must bind the queue with the same empty
        # key -- otherwise every message is unroutable on the direct
        # exchange and silently dropped (or returned, with mandatory=True).
        try:
            self.channel.queue_bind(
                queue=queue_name,
                exchange=exchange_name,
                routing_key=routing_key,
            )
        except Exception as err:
            self.logger.error(
                "Error binding queue %s to exchange %s (key=%r): %s",
                queue_name,
                exchange_name,
                routing_key,
                err,
            )

    # ----------------------------------------------------------------- I/O
    def send(self, data, exchange: str = "", key: str = "") -> bool:
        payload = data if isinstance(data, (bytes, bytearray)) else str(data)
        try:
            # mandatory=True + confirm_delivery() means we block until the
            # broker either acks the publish *and* routed it to a queue, or
            # raises (UnroutableError / ChannelClosed / ConnectionClosed).
            self.channel.basic_publish(
                exchange=exchange,
                routing_key=key,
                body=payload,
                mandatory=True,
            )
            return True
        except Exception as err:
            self.logger.error(
                "RABBITMQ %s,%s publish failed: %s", exchange, key, err
            )
            self.conn = False
            self.close()
            return False

    def recv(self, queue_name: str = "") -> str:
        try:
            if self.channel is None or not self.channel.is_open:
                raise Exception("Channel is not open")
            msg_obj = self.channel.basic_get(queue=queue_name, auto_ack=True)
        except Exception as err:
            self.logger.error(
                "RABBITMQ %s lost connection during recv: %s", queue_name, err
            )
            self.conn = False
            self.connect()
            time.sleep(0.5)
            return ""

        if msg_obj[2] is None:
            return "{}"
        return msg_obj[2].decode("utf-8")

    def callback(self, ch, method, properties, body) -> None:
        self.message = body
        self.logger.info("[ DELIVERY TAG ]: %s", method.delivery_tag)
        ch.basic_nack(delivery_tag=method.delivery_tag)

    def size(self) -> int:
        return self.channel.get_waiting_message_count()
