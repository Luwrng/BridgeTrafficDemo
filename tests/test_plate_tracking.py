import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from plate_tracking import PlateTracker


class TrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This fixture is a model response captured from the supplied RTSP image, not a seeded plate list.
        fixture = json.loads((ROOT / "artifacts/ui-check/real-frame-result.json").read_text(encoding="utf-8-sig"))
        cls.observed = next(item for item in fixture["detections"] if item["plate"])

    def observation(self, dx=0):
        item = copy.deepcopy(self.observed)
        item["box"][0] += dx
        return item

    def test_confirmation_needs_three_distinct_frames(self):
        tracker = PlateTracker()
        for frame in range(3):
            result = tracker.update([self.observation(frame * 2)], frame * .4, str(frame))[0]
            self.assertEqual(result["confirmed"], frame == 2)
        self.assertTrue(result["newlyConfirmed"])
        self.assertFalse(tracker.update([self.observation(7)], 1.2, "3")[0]["newlyConfirmed"])

    def test_duplicate_frame_does_not_add_evidence(self):
        tracker = PlateTracker()
        for time in (0, .4, .8):
            result = tracker.update([self.observation()], time, "same-frame")[0]
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["evidenceCount"], 1)

    def test_two_regions_with_same_plate_keep_independent_tracks(self):
        tracker = PlateTracker()
        first = self.observation(); second = self.observation(400)
        initial = tracker.update([first, second], 0, "0")
        self.assertNotEqual(initial[0]["trackId"], initial[1]["trackId"])
        moved = tracker.update([self.observation(402), self.observation(2)], .4, "1")
        self.assertEqual(moved[0]["trackId"], initial[1]["trackId"])
        self.assertEqual(moved[1]["trackId"], initial[0]["trackId"])

    def test_empty_frame_ends_pass_and_same_plate_can_return(self):
        tracker = PlateTracker()
        old = tracker.update([self.observation()], 0, "0")[0]["trackId"]
        self.assertEqual(tracker.update([], .4, "1"), [])
        returned = tracker.update([self.observation()], .8, "2")[0]
        self.assertNotEqual(returned["trackId"], old)
        self.assertFalse(returned["confirmed"])

    def test_unreadable_frame_does_not_publish_previous_confirmed_plate(self):
        tracker = PlateTracker()
        for frame in range(3):
            tracker.update([self.observation()], frame * .4, str(frame))
        unreadable = self.observation(); unreadable["plate"] = None; unreadable["confidence"] = 0
        result = tracker.update([unreadable], 1.2, "3")[0]
        self.assertFalse(result["confirmed"])
        self.assertIsNone(result["plate"])
        self.assertEqual(result["state"], "unreadable")

    def test_scene_change_requires_new_tracking_evidence(self):
        tracker = PlateTracker()
        old = tracker.update([self.observation()], 0, "0")[0]["trackId"]
        changed = tracker.update([self.observation()], .4, "1", scene_cut=True)[0]
        self.assertNotEqual(changed["trackId"], old)
        self.assertFalse(changed["confirmed"])

    def test_same_position_but_different_vehicle_appearance_starts_new_pass(self):
        tracker = PlateTracker()
        first = self.observation(); first["appearance"] = [1, 0, 0, 0, 0, 0, 0, 0] * 3
        old = tracker.update([first], 0, "0")[0]["trackId"]
        second = self.observation(); second["appearance"] = [0, 0, 0, 0, 0, 0, 0, 1] * 3
        changed = tracker.update([second], .4, "1")[0]
        self.assertNotEqual(changed["trackId"], old)
        self.assertFalse(changed["confirmed"])


if __name__ == "__main__":
    unittest.main()
