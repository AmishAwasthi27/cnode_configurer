"""Persistent camera settings used when the configured target is this PC."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.constants import ROOT_DIR


class LocalCameraConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ROOT_DIR / "data" / "local_cameras.json"

    def list(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, camera_id: str, payload: dict[str, Any]) -> None:
        cameras = self.list()
        cameras[camera_id] = dict(payload)
        self._write(cameras)

    def delete(self, camera_id: str) -> None:
        cameras = self.list()
        cameras.pop(camera_id, None)
        self._write(cameras)

    def _write(self, cameras: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cameras, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


local_camera_config = LocalCameraConfig()
