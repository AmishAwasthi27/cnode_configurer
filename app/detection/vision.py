"""OpenCV DNN category detection and conservative visual-reference matching."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np


COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


@dataclass(frozen=True)
class Detection:
    category: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int
    identity_status: str = "Unknown / unverified"
    object_name: str = ""
    match_score: float = 0.0


def _letterbox(image: np.ndarray, size: int = 640) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    resized = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    return canvas, scale


class YoloXDetector:
    """COCO YOLOX ONNX inference using the OpenCV DNN CPU backend."""

    INPUT_SIZE = 640
    STRIDES = (8, 16, 32)

    def __init__(self, model_path: Path | str, confidence: float = 0.35, nms: float = 0.5) -> None:
        model = Path(model_path)
        if not model.is_file():
            raise FileNotFoundError(f"Detection model not found: {model}")
        self.net = cv2.dnn.readNet(str(model))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.confidence = float(confidence)
        self.nms = float(nms)
        self._grids, self._strides = self._anchors()

    @classmethod
    def _anchors(cls) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for stride in cls.STRIDES:
            h = w = cls.INPUT_SIZE // stride
            xv, yv = np.meshgrid(np.arange(w), np.arange(h))
            grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((1, grid.shape[1], 1), stride))
        return np.concatenate(grids, axis=1), np.concatenate(strides, axis=1)

    def detect(self, image: np.ndarray, confidence: Optional[float] = None) -> list[Detection]:
        if image is None or image.size == 0:
            return []
        original_h, original_w = image.shape[:2]
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        padded, scale = _letterbox(rgb, self.INPUT_SIZE)
        blob = np.transpose(padded.astype(np.float32), (2, 0, 1))[None, ...]
        self.net.setInput(blob)
        raw = self.net.forward(self.net.getUnconnectedOutLayersNames())[0]
        rows = np.asarray(raw).reshape(-1, 85).copy()
        rows[:, :2] = (rows[:, :2] + self._grids[0]) * self._strides[0]
        rows[:, 2:4] = np.exp(np.clip(rows[:, 2:4], -20, 20)) * self._strides[0]
        scores = rows[:, 4:5] * rows[:, 5:]
        labels = scores.argmax(axis=1)
        confs = scores[np.arange(scores.shape[0]), labels]
        threshold = self.confidence if confidence is None else float(confidence)
        valid = np.flatnonzero(confs >= threshold)
        if not len(valid):
            return []

        boxes = []
        selected = []
        for index in valid:
            cx, cy, width, height = rows[index, :4]
            x1 = int(max(0, min(original_w - 1, (cx - width / 2) / scale)))
            y1 = int(max(0, min(original_h - 1, (cy - height / 2) / scale)))
            x2 = int(max(x1 + 1, min(original_w, (cx + width / 2) / scale)))
            y2 = int(max(y1 + 1, min(original_h, (cy + height / 2) / scale)))
            boxes.append([x1, y1, x2 - x1, y2 - y1])
            selected.append((int(labels[index]), float(confs[index]), x1, y1, x2, y2))

        kept: list[int] = []
        if hasattr(cv2.dnn, "NMSBoxesBatched"):
            indexes = cv2.dnn.NMSBoxesBatched(boxes, [x[1] for x in selected], [x[0] for x in selected], threshold, self.nms)
            kept = np.asarray(indexes).reshape(-1).astype(int).tolist() if len(indexes) else []
        else:  # compatibility with older OpenCV builds
            for label in sorted({x[0] for x in selected}):
                positions = [i for i, x in enumerate(selected) if x[0] == label]
                indexes = cv2.dnn.NMSBoxes([boxes[i] for i in positions], [selected[i][1] for i in positions], threshold, self.nms)
                kept.extend(positions[int(i)] for i in np.asarray(indexes).reshape(-1))
        return [
            Detection(COCO_CLASSES[selected[i][0]], selected[i][1], *selected[i][2:])
            for i in kept
        ]


def _feature_score(reference: np.ndarray, candidate: np.ndarray) -> float:
    """Homography-backed local-feature score; weak texture returns no match."""
    if reference is None or candidate is None or reference.size == 0 or candidate.size == 0:
        return 0.0
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY) if reference.ndim == 3 else reference
    can_gray = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY) if candidate.ndim == 3 else candidate
    detector = cv2.ORB_create(nfeatures=1200, scaleFactor=1.2, nlevels=8)
    kp1, des1 = detector.detectAndCompute(ref_gray, None)
    kp2, des2 = detector.detectAndCompute(can_gray, None)
    if des1 is None or des2 is None or min(len(kp1), len(kp2)) < 8:
        return 0.0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des1, des2, k=2)
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < 0.72 * b.distance]
    if len(good) < 8:
        return 0.0
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    inliers = int(mask.sum()) if mask is not None else 0
    if inliers < 8:
        return 0.0
    return inliers / max(1, min(len(kp1), len(kp2)))


class ReferenceMatcher:
    """Cache gallery descriptors so each live frame only computes its features once."""

    def __init__(self, references: Iterable[dict]) -> None:
        self._orb = cv2.ORB_create(nfeatures=1200, scaleFactor=1.2, nlevels=8)
        self._references: list[tuple[str, str, list, np.ndarray | None]] = []
        for reference in references:
            path = Path(str(reference.get("path", "")))
            image = cv2.imread(str(path)) if path.is_file() else None
            if image is None:
                self._references.append((str(reference.get("category", "")), str(reference.get("name", "")), [], None))
                continue
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
            keypoints, descriptors = self._orb.detectAndCompute(gray, None)
            self._references.append((str(reference.get("category", "")), str(reference.get("name", "")), keypoints, descriptors))

    def match(self, category: str, candidate: np.ndarray, *, threshold: float = 0.32, minimum_margin: float = 0.08) -> tuple[str, str, float]:
        all_refs = self._references
        refs = [item for item in all_refs if item[0].casefold() == category.casefold()]
        if candidate is None or candidate.size == 0:
            return "Same category · identity unverified", "", 0.0
        gray = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY) if candidate.ndim == 3 else candidate
        kp_candidate, desc_candidate = self._orb.detectAndCompute(gray, None)
        scored: list[tuple[float, str]] = []
        if desc_candidate is not None:
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
            for _, name, kp_reference, desc_reference in refs:
                if desc_reference is None or min(len(kp_reference), len(kp_candidate)) < 8:
                    scored.append((0.0, name))
                    continue
                pairs = matcher.knnMatch(desc_reference, desc_candidate, k=2)
                good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < 0.72 * b.distance]
                score = 0.0
                if len(good) >= 8:
                    src = np.float32([kp_reference[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                    dst = np.float32([kp_candidate[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
                    inliers = int(mask.sum()) if mask is not None else 0
                    if inliers >= 8:
                        score = inliers / max(1, min(len(kp_reference), len(kp_candidate)))
                scored.append((score, name))
        by_object: dict[str, float] = {}
        for score, name in scored:
            by_object[name] = max(score, by_object.get(name, 0.0))
        scored = sorted(((score, name) for name, score in by_object.items()), reverse=True)
        if scored and scored[0][0] >= threshold and (len(scored) == 1 or scored[0][0] - scored[1][0] >= minimum_margin):
            return "Likely exact visual match", scored[0][1], scored[0][0]
        if refs:
            return "Same category · identity unverified", "", scored[0][0] if scored else 0.0
        if all_refs:
            return "Different category", "", 0.0
        return "Category detected · no reference", "", 0.0


def decide_identity(
    category: str,
    candidate: np.ndarray,
    references: Iterable[dict],
    *,
    threshold: float = 0.32,
    minimum_margin: float = 0.08,
) -> tuple[str, str, float]:
    """Return a conservative identity label, reference name, and visual score."""
    return ReferenceMatcher(references).match(
        category, candidate, threshold=threshold, minimum_margin=minimum_margin,
    )


def annotate_detections(image: np.ndarray, detections: Iterable[Detection]) -> np.ndarray:
    """Draw category, confidence, identity decision and box onto a copy."""
    result = image.copy()
    for det in detections:
        color = (30, 170, 70) if det.object_name else (20, 150, 230)
        cv2.rectangle(result, (det.x1, det.y1), (det.x2, det.y2), color, 2)
        status = det.object_name if det.object_name else det.identity_status
        label = f"{det.category} {det.confidence:.0%} · {status}"
        y = max(18, det.y1 - 7)
        cv2.putText(result, label, (det.x1, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
    return result

