"""Persistent local gallery of registered physical-object reference images."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class ReferenceStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.images_dir = self.root / "images"
        self.index_path = self.root / "objects.json"

    @staticmethod
    def _safe_stem(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")[:64] or "object"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and isinstance(data.get("objects"), list) else {"objects": []}
        except (OSError, ValueError):
            return {"objects": []}

    def objects(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._read()["objects"] if isinstance(item, dict)]

    def references(self) -> list[dict[str, str]]:
        refs = []
        for item in self.objects():
            for filename in item.get("references", []):
                refs.append({"name": str(item.get("name", "")), "category": str(item.get("category", "")), "path": str(self.images_dir / filename)})
        return refs

    def add_reference(self, name: str, category: str, image: np.ndarray) -> dict[str, Any]:
        name, category = name.strip(), category.strip()
        if not name or not category:
            raise ValueError("Object name and detected category are required.")
        if image is None or image.size == 0:
            raise ValueError("The selected detection has no image crop.")
        self.images_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{self._safe_stem(name)}_{uuid.uuid4().hex[:10]}.jpg"
        target = self.images_dir / filename
        if not cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"Could not save reference image to {target}")
        data = self._read()
        objects = data["objects"]
        item = next((o for o in objects if str(o.get("name", "")).casefold() == name.casefold()), None)
        if item is None:
            item = {"id": uuid.uuid4().hex, "name": name, "category": category, "references": [], "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            objects.append(item)
        if str(item.get("category", "")).casefold() != category.casefold():
            target.unlink(missing_ok=True)
            raise ValueError(f"'{name}' is already registered as category '{item.get('category')}'.")
        item.setdefault("references", []).append(filename)
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.index_path)
        return dict(item)

