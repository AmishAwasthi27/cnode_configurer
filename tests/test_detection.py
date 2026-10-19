from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.detection.store import ReferenceStore
from app.detection.vision import YoloXDetector, decide_identity
from app.managers.local_manager import LocalManager


class DetectionDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = np.zeros((120, 120, 3), dtype=np.uint8)

    def test_unregistered_category_is_reported_without_identity_claim(self) -> None:
        status, name, score = decide_identity("bottle", self.frame, [])
        self.assertEqual(status, "Category detected · no reference")
        self.assertEqual(name, "")
        self.assertEqual(score, 0.0)

    def test_similar_category_without_visual_evidence_stays_unverified(self) -> None:
        refs = [{"name": "Bottle A", "category": "bottle", "path": "missing.jpg"}]
        status, name, _ = decide_identity("bottle", self.frame, refs)
        self.assertEqual(status, "Same category · identity unverified")
        self.assertEqual(name, "")

    def test_different_category_is_not_an_exact_object_match(self) -> None:
        refs = [{"name": "Bottle A", "category": "bottle", "path": "missing.jpg"}]
        status, name, _ = decide_identity("chair", self.frame, refs)
        self.assertEqual(status, "Different category")
        self.assertEqual(name, "")

    def test_additional_references_are_persisted_for_same_object(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            store = ReferenceStore(directory)
            first = np.full((40, 40, 3), 80, dtype=np.uint8)
            second = np.full((40, 40, 3), 160, dtype=np.uint8)
            store.add_reference("Bottle A", "bottle", first)
            store.add_reference("Bottle A", "bottle", second)
            objects = store.objects()
            self.assertEqual(len(objects), 1)
            self.assertEqual(len(objects[0]["references"]), 2)
            self.assertEqual(len(store.references()), 2)

    def test_multiple_views_of_one_registered_object_do_not_create_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            image = np.random.default_rng(4).integers(0, 256, (180, 140, 3), dtype=np.uint8)
            store = ReferenceStore(directory)
            store.add_reference("Bottle A", "bottle", image)
            store.add_reference("Bottle A", "bottle", image.copy())
            status, name, score = decide_identity("bottle", image, store.references())
            self.assertEqual(status, "Likely exact visual match")
            self.assertEqual(name, "Bottle A")
            self.assertGreaterEqual(score, 0.32)

    def test_coco_yolox_model_loads_and_runs_on_a_frame(self) -> None:
        model = Path(__file__).parents[1] / "models" / "object_detection_yolox_2022nov_int8.onnx"
        if not model.is_file():
            self.skipTest("Bundled model asset is unavailable")
        detector = YoloXDetector(model, confidence=0.99)
        self.assertEqual(detector.detect(np.zeros((240, 320, 3), dtype=np.uint8)), [])

    def test_local_manager_persists_profile_template_and_matches_it(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            manager = LocalManager(Path(directory))
            workflow = manager.get_workflow("Local inspection")
            profile = manager.create_profile(
                workflow_id=workflow.id,
                name="Bottle template",
                algorithm="template_matching",
                data={"match_method": "TM_CCOEFF_NORMED", "match_threshold": 0.8},
                is_default=True,
            )
            image = np.random.default_rng(4).integers(0, 255, (48, 36, 3), dtype=np.uint8)
            source = Path(directory) / "sample.png"
            import cv2
            self.assertTrue(cv2.imwrite(str(source), image))
            manager.upload_template(profile.id, str(source), name="Bottle A")
            name, score = manager.match_template(profile.id, image.copy())
            self.assertEqual(name, "Bottle A")
            self.assertGreaterEqual(score, 0.8)
            wrong_class, _ = manager.match_template(profile.id, image.copy(), category="person")
            self.assertEqual(wrong_class, "")
            self.assertEqual(manager.list_profiles(workflow.id)[0].name, "Bottle template")


if __name__ == "__main__":
    unittest.main()
