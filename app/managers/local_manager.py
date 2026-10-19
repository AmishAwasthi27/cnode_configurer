"""Embedded cnode_manager-compatible storage for standalone laptop mode."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.constants import ROOT_DIR
from app.detection.vision import COCO_CLASSES


@dataclass
class LocalWorkflow:
    id: int
    name: str


@dataclass
class LocalProfile:
    id: int
    workflow_id: int
    name: str
    algorithm: str
    data: dict[str, Any]
    is_default: bool = False


@dataclass
class LocalTemplate:
    id: int
    profile_id: int
    name: str
    path: str
    url: str = ""
    handler_relative: str = ""


class LocalManager:
    """Small persistent local backend for workflows, profiles and templates."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or ROOT_DIR / "data" / "local_manager"
        self.db_path = self.root / "manager.json"
        self.template_dir = self.root / "templates"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("workflows", [{"id": 1, "name": "Local inspection"}])
                data.setdefault("profiles", [])
                data.setdefault("templates", [])
                return data
        except (OSError, ValueError):
            pass
        return {"workflows": [{"id": 1, "name": "Local inspection"}], "profiles": [], "templates": []}

    def _write(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.db_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.db_path)

    def list_workflows(self) -> dict[str, int]:
        return {item["name"]: item["id"] for item in self._read()["workflows"]}

    def get_workflow(self, name: str) -> LocalWorkflow:
        for item in self._read()["workflows"]:
            if item["name"] == name:
                return LocalWorkflow(**item)
        raise KeyError(name)

    def set_active_workflow(self, name: str) -> None:
        data = self._read()
        if name not in {item["name"] for item in data["workflows"]}:
            raise KeyError(name)
        data["active_workflow"] = name
        self._write(data)

    def active_workflow(self) -> LocalWorkflow:
        data = self._read()
        selected = data.get("active_workflow")
        for item in data["workflows"]:
            if item["name"] == selected:
                return LocalWorkflow(**item)
        return LocalWorkflow(**data["workflows"][0])

    def get_handler_config(self) -> dict[str, Any]:
        from app.managers.local_camera_config import local_camera_config
        return {"cameras": local_camera_config.list()}

    def list_profiles(self, workflow_id: int) -> list[LocalProfile]:
        return [LocalProfile(**item) for item in self._read()["profiles"] if item["workflow_id"] == workflow_id]

    def get_profile(self, profile_id: int) -> LocalProfile:
        for item in self._read()["profiles"]:
            if item["id"] == profile_id:
                return LocalProfile(**item)
        raise KeyError(profile_id)

    def create_profile(self, *, workflow_id: int, name: str, algorithm: str, data: dict[str, Any], is_default: bool) -> LocalProfile:
        db = self._read()
        profile_id = max((item["id"] for item in db["profiles"]), default=0) + 1
        if is_default:
            for item in db["profiles"]:
                if item["workflow_id"] == workflow_id:
                    item["is_default"] = False
        item = {"id": profile_id, "workflow_id": workflow_id, "name": name, "algorithm": algorithm, "data": data, "is_default": is_default}
        db["profiles"].append(item)
        self._write(db)
        return LocalProfile(**item)

    def update_profile(self, profile_id: int, *, name: str, algorithm: str, is_default: bool, data: dict[str, Any]) -> LocalProfile:
        db = self._read()
        item = next(value for value in db["profiles"] if value["id"] == profile_id)
        if is_default:
            for other in db["profiles"]:
                if other["workflow_id"] == item["workflow_id"]:
                    other["is_default"] = False
        item.update(name=name, algorithm=algorithm, is_default=is_default, data=data)
        self._write(db)
        return LocalProfile(**item)

    def delete_profile(self, profile_id: int) -> None:
        db = self._read()
        db["profiles"] = [item for item in db["profiles"] if item["id"] != profile_id]
        deleted = [item for item in db["templates"] if item["profile_id"] == profile_id]
        db["templates"] = [item for item in db["templates"] if item["profile_id"] != profile_id]
        for item in deleted:
            Path(item["path"]).unlink(missing_ok=True)
        self._write(db)

    def list_templates(self, profile_id: int) -> list[LocalTemplate]:
        return [LocalTemplate(**item) for item in self._read()["templates"] if item["profile_id"] == profile_id]

    def template_categories(self, profile_id: int) -> set[str]:
        return {
            category
            for template in self.list_templates(profile_id)
            if (category := self._category_from_name(template.name))
        }

    def upload_template(self, profile_id: int, path: str, *, name: str) -> LocalTemplate:
        db = self._read()
        template_id = max((item["id"] for item in db["templates"]), default=0) + 1
        self.template_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(path).suffix or ".png"
        target = self.template_dir / f"template_{template_id}{suffix}"
        shutil.copy2(path, target)
        item = {"id": template_id, "profile_id": profile_id, "name": name, "path": str(target), "url": "", "handler_relative": ""}
        db["templates"].append(item)
        self._write(db)
        return LocalTemplate(**item)

    def delete_template(self, template_id: int) -> None:
        db = self._read()
        matches = [item for item in db["templates"] if item["id"] == template_id]
        db["templates"] = [item for item in db["templates"] if item["id"] != template_id]
        for item in matches:
            Path(item["path"]).unlink(missing_ok=True)
        self._write(db)

    @staticmethod
    def _category_from_name(name: str) -> str:
        normalized = "".join(char for char in name.casefold() if char.isalnum())
        for category in sorted(COCO_CLASSES, key=len, reverse=True):
            token = "".join(char for char in category.casefold() if char.isalnum())
            if normalized.startswith(token):
                return category
        return ""

    def match_template(self, profile_id: int, image: np.ndarray, *, category: str = "") -> tuple[str, float]:
        """Return best template name and normalized score for an image crop."""
        profile = self.get_profile(profile_id)
        method_name = profile.data.get("match_method", "TM_CCOEFF_NORMED")
        method = getattr(cv2, method_name, cv2.TM_CCOEFF_NORMED)
        threshold = float(profile.data.get("match_threshold", 0.5))
        best_name, best_score = "", -1.0
        for template in self.list_templates(profile_id):
            expected_category = self._category_from_name(template.name)
            if expected_category and category and expected_category.casefold() != category.casefold():
                continue
            reference = cv2.imread(template.path)
            if reference is None or image is None or image.size == 0:
                continue
            image_h, image_w = image.shape[:2]
            ref_h, ref_w = reference.shape[:2]
            # Templates may be captured at a different zoom. Search a useful
            # range of sizes within each detected object crop instead of
            # stretching the whole crop to one exact size.
            for scale in (0.25, 0.33, 0.42, 0.5, 0.6, 0.72, 0.85, 1.0, 1.15, 1.3, 1.5):
                width, height = round(ref_w * scale), round(ref_h * scale)
                if width < 8 or height < 8 or width > image_w or height > image_h:
                    continue
                scaled_ref = cv2.resize(reference, (width, height))
                result = cv2.matchTemplate(image, scaled_ref, method)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                raw = float(min_val if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED) else max_val)
                loc = min_loc if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED) else max_loc
                patch = image[loc[1]:loc[1] + height, loc[0]:loc[0] + width]
                if method in (cv2.TM_SQDIFF, cv2.TM_SQDIFF_NORMED):
                    if method == cv2.TM_SQDIFF_NORMED:
                        score = 1.0 - raw
                    else:
                        energy = float(np.sum(patch.astype(np.float32) ** 2) + np.sum(scaled_ref.astype(np.float32) ** 2))
                        score = 1.0 - min(1.0, raw / max(energy, 1e-12))
                elif method in (cv2.TM_CCOEFF, cv2.TM_CCORR):
                    if method == cv2.TM_CCOEFF:
                        left = patch.astype(np.float32) - float(patch.mean())
                        right = scaled_ref.astype(np.float32) - float(scaled_ref.mean())
                        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
                        score = (raw / max(denominator, 1e-12) + 1.0) / 2.0
                    else:
                        denominator = float(np.linalg.norm(patch) * np.linalg.norm(scaled_ref))
                        score = raw / max(denominator, 1e-12)
                else:
                    score = raw
                score = max(0.0, min(1.0, score))
                if score > best_score:
                    best_name, best_score = template.name, score
        if best_score >= threshold:
            return best_name, best_score
        return "", max(0.0, best_score)


local_manager = LocalManager()
