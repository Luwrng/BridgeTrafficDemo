"""Regression images/labels were captured from the user's actual RTSP feeds.

These fixtures are test expectations; the application never reads their plates.
"""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "artifacts/ocr-python-deps"))
sys.path.insert(0, str(ROOT / "tools"))
from paddle_plate_ocr import PaddlePlateOCR, plate_candidates
from local_ocr import recognize


class PaddleOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = json.loads((ROOT / "artifacts/ui-check/paddle-ocr-lines.json").read_text(encoding="utf-8"))
        cls.truth = json.loads((ROOT / "artifacts/ui-check/paddle-groundtruth.json").read_text(encoding="utf-8"))

    def test_real_two_line_plate_is_combined_in_reading_order(self):
        candidates = plate_candidates(list(reversed(self.lines)))
        self.assertEqual(candidates[0][0], self.truth[0]["plate"])

    def test_uncertain_line_is_not_published_as_plate(self):
        lines = copy.deepcopy(self.lines)
        lines[-1][2] = .69
        self.assertEqual(plate_candidates(lines), [])

    def test_separate_objects_cannot_form_one_plate(self):
        lines = copy.deepcopy(self.lines)
        for point in lines[-1][0]:
            point[0] += 500
        self.assertEqual(plate_candidates(lines), [])

    def test_two_different_plates_in_one_crop_remain_uncertain(self):
        lines = copy.deepcopy(self.lines)
        fixture = json.loads((ROOT / "artifacts/ui-check/paddle-live-verification.json").read_text(encoding="utf-8"))
        other = next(item for item in fixture["liveB"][-1]["detections"] if item["confirmed"])
        lines.append([copy.deepcopy(lines[0][0]), other["rawText"], other["confidence"]])
        reader = PaddlePlateOCR.__new__(PaddlePlateOCR)
        reader.engine = lambda *_args, **_kwargs: (lines, None)
        from PIL import Image
        self.assertIsNone(reader.read(Image.new("RGB", (1, 1)))[0])

    def test_yolo_crops_and_paddleocr_read_actual_frames(self):
        for fixture in self.truth:
            with self.subTest(image=fixture["image"]):
                detections, width, height = recognize((ROOT / "artifacts/ui-check" / fixture["image"]).read_bytes())
                found = next(item for item in detections if item["plate"] == fixture["plate"])
                self.assertGreaterEqual(found["confidence"], .70)
                x, y, w, h = found["box"]
                self.assertTrue(0 <= x < x + w <= width and 0 <= y < y + h <= height)

    def test_real_wall_has_no_plate(self):
        detections, _, _ = recognize((ROOT / "artifacts/ui-check/real-wall-negative.jpg").read_bytes())
        self.assertFalse(any(item["plate"] for item in detections))


if __name__ == "__main__":
    unittest.main()
