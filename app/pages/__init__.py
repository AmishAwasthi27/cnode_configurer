"""Application pages (stacked widgets)."""
from __future__ import annotations

from app.pages.cameras import CamerasPage
from app.pages.dashboard import DashboardPage
from app.pages.detection_profiles import DetectionProfilesPage
from app.pages.io_test import IOTestPage
from app.pages.updates import UpdatesPage
from app.pages.workflows import WorkflowsPage
from app.pages.live_detection import LiveDetectionPage

__all__ = [
    "CamerasPage",
    "DashboardPage",
    "DetectionProfilesPage",
    "IOTestPage",
    "UpdatesPage",
    "WorkflowsPage",
    "LiveDetectionPage",
]
