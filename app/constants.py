"""Application-wide constants."""
from __future__ import annotations

from pathlib import Path

APP_NAME = "CNODE Configurer"
APP_ORG = "STABLE_DYNAMICS"

ROOT_DIR = Path(__file__).resolve().parents[1]
CONNECTIONS_FILE = ROOT_DIR / "connections.json"

DEFAULT_HOST = "cnode1.local"

# Service ports on the cnode device.
PORT_CAMERA_STREAM = 8060
PORT_DETECTION_STREAM = 8061
PORT_SUPERVISOR = 9001
PORT_RMQ = 5672
PORT_SSH = 22
PORT_MANAGER_API = 8000

SERVICES = {
    "camera_stream": PORT_CAMERA_STREAM,
    "detection_stream": PORT_DETECTION_STREAM,
    "supervisor": PORT_SUPERVISOR,
    "rabbitmq": PORT_RMQ,
    "ssh": PORT_SSH,
    "cnode_manager": PORT_MANAGER_API,
}

# Services we actually probe for reachability from the UI.
# SSH + Supervisor are deferred: they'll be managed separately later and
# including them in the probe just produces misleading "partial" statuses.
PROBED_SERVICES = {
    "camera_stream": PORT_CAMERA_STREAM,
    "detection_stream": PORT_DETECTION_STREAM,
    "rabbitmq": PORT_RMQ,
    "cnode_manager": PORT_MANAGER_API,
}

# Mode identifiers.
MODE_PRODUCTION = "production"
MODE_MAINTENANCE = "maintenance"

# Theme accents.
ACCENT_PRODUCTION = "#2E3A59"  # grayish navy blue
ACCENT_MAINTENANCE = "#E0A800"  # amber yellow
