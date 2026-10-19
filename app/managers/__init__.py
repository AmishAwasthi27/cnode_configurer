"""Singleton managers shared across the application."""
from __future__ import annotations

from app.managers.account_manager import AccountManager
from app.managers.api_client import ManagerClient
from app.managers.commander import Commander
from app.managers.connection_manager import ConnectionManager
from app.managers.notification_manager import NotificationManager
from app.managers.status_manager import StatusManager

connection_manager = ConnectionManager()
notification_manager = NotificationManager()
account_manager = AccountManager()
api_client = ManagerClient(connection_manager)
status_manager = StatusManager(connection_manager)
commander = Commander(connection_manager)

__all__ = [
    "AccountManager",
    "Commander",
    "ConnectionManager",
    "ManagerClient",
    "NotificationManager",
    "StatusManager",
    "connection_manager",
    "notification_manager",
    "account_manager",
    "api_client",
    "status_manager",
    "commander",
]
