"""Hash nguồn dùng chung được kiểm tra trong RAM; không kết nối camera thật."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from camera_streams import CameraStream


class SourceIdentityTests(unittest.TestCase):
    def status(self,url):
        camera = CameraStream("A")
        camera.url = url; camera.mode = "rtsp"
        return camera.status()

    def test_same_feed_has_same_identity_despite_different_accounts(self):
        first=self.status("rtsp://test-user:unit-secret@127.0.0.1:554/camera")
        second=self.status("rtsp://another-user:another-secret@127.0.0.1/camera")
        self.assertEqual(first["sourceFingerprint"],second["sourceFingerprint"])
        self.assertNotIn("unit-secret",json.dumps(first))
        self.assertNotIn("rtsp://",json.dumps(first))

    def test_separate_feeds_have_different_identities(self):
        first=self.status("rtsp://127.0.0.1:554/camera-a")
        second=self.status("rtsp://127.0.0.1:554/camera-b")
        self.assertNotEqual(first["sourceFingerprint"],second["sourceFingerprint"])


if __name__ == "__main__": unittest.main()
