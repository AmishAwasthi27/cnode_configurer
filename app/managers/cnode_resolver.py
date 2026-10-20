"""Resolve the runtime cnode, falling back to the laptop without blocking UI."""
from __future__ import annotations

import socket
from typing import Any, Callable
from urllib.parse import urlparse


def probe_remote(url: str, timeout: float = 0.8) -> bool:
    """Return whether the remote cnode manager endpoint accepts TCP connections."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_cnode(
    settings: dict[str, Any],
    remote_probe: Callable[[str], bool] = probe_remote,
) -> dict[str, str | None]:
    """Use a reachable configured remote manager; otherwise select this laptop.

    Supports the current connection settings (``host`` + ``manager_port``) and
    a direct ``cnode_url`` for forward compatibility with URL-based settings.
    """
    remote_url = str(settings.get("cnode_url") or "").strip()
    host = str(settings.get("host") or "").strip()
    if not remote_url and host and host.casefold() not in {
        "localhost", "127.0.0.1", "::1", "this-device",
    }:
        port = int(settings.get("manager_port") or 8000)
        remote_url = f"http://{host}:{port}"
    if remote_url and remote_probe(remote_url):
        parsed = urlparse(remote_url if "://" in remote_url else f"http://{remote_url}")
        return {"mode": "remote", "url": remote_url, "host": parsed.hostname or host}
    return {"mode": "local", "url": None, "host": "this-device"}
