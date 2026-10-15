"""Kiểm tra adapter OCR -> hồ sơ; mock broker và lưu toàn bộ trong DB tạm."""
from datetime import datetime, timedelta, timezone
import base64
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import local_ocr
from vehicle_records import PassageRecorder
from test_vehicle_records import RecordFixture


class CaptureAdapterTests(RecordFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(patch.stopall)
        patch.object(local_ocr, "VISIT_STORE", self.store).start()
        patch.object(local_ocr, "PASSAGE_RECORDER", PassageRecorder(self.store)).start()
        self.status = dict(configured=True, streamAvailable=True, revision=3, frameId=10, sharedFrom=None)
        self.broker = patch.object(local_ocr, "urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(json.dumps(self.status).encode())).start()

    def frame(self, position, *, kind="live", revision="3", frame_id="1", at=None):
        headers = {"X-Source-Kind":kind, "X-Camera-Revision":revision, "X-Captured-At":at or datetime.now(timezone.utc).isoformat()}
        detection = dict(box=[20,position * 100 - 5,20,10],trackId=1,plate="51K73109",confidence=.9,confirmed=True,evidenceCount=3,
                         crop="data:image/jpeg;base64," + base64.b64encode(self.crop).decode())
        return local_ocr.record_camera_passage(headers,"A","isolated-capture-test",[detection],self.scene,100,100,float(frame_id),frame_id)

    def test_private_live_source_records_confirmed_plate_without_line(self):
        result = self.frame(.3)
        self.assertEqual(result["state"], "entry")
        self.assertEqual(self.store.list_visits()["total"],1)

    def test_same_rtsp_assigned_to_gate_b_records_exit_and_exit_evidence(self):
        self.status['sourceFingerprint'] = 'a' * 64
        entry = self.frame(.3)
        at = (datetime.fromisoformat(entry['occupancy']['vehicle']['entered_at']) + timedelta(seconds=1)).isoformat()
        headers = {'X-Source-Kind':'live','X-Camera-Revision':'3','X-Captured-At':at}
        detection = dict(plate='51K73109',confirmed=True,evidenceCount=3,confidence=.9,
                         crop='data:image/jpeg;base64,'+base64.b64encode(self.crop).decode())
        exited = local_ocr.record_camera_passage(headers,'B','capture-gate-b',[detection],self.scene,100,100,2,'2')
        self.assertEqual(exited['state'],'exit')
        self.assertEqual(exited['visitIds'],entry['visitIds'])
        self.assertFalse(exited['occupancy']['occupied'])
        visit = self.store.detail(entry['visitIds'][0])
        self.assertEqual((visit['entry_camera'],visit['exit_camera']),('A','B'))
        self.assertEqual([passage['direction'] for passage in visit['passages']],['entry','exit'])

    def test_video_file_is_preview_without_reading_live_camera_status(self):
        self.frame(.3,kind="file")
        result = self.frame(.7,kind="file",frame_id="2")
        self.broker.assert_not_called()
        self.assertEqual(result["state"], "preview-only")
        self.assertEqual(self.store.list_visits()["total"],0)

    def test_changed_camera_revision_rejects_old_frame(self):
        result = self.frame(.3,revision="2")
        self.assertEqual(result["state"], "source-changed")
        self.assertEqual(self.store.list_visits()["total"],0)

    def test_old_live_frame_does_not_create_a_real_visit(self):
        at = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        self.assertEqual(self.frame(.3,at=at)["state"], "stale-frame")
        self.assertEqual(self.store.list_visits()["total"],0)

    def test_slow_inference_does_not_drop_a_frame_received_while_fresh(self):
        now = datetime.now(timezone.utc)
        captured = now - timedelta(seconds=30)
        headers = {'X-Source-Kind':'live','X-Camera-Revision':'3','X-Captured-At':captured.isoformat()}
        detection = dict(plate='51K73109',confirmed=True,evidenceCount=3,confidence=.9,
                         crop='data:image/jpeg;base64,'+base64.b64encode(self.crop).decode())
        result = local_ocr.record_camera_passage(headers,'A','slow-test',[detection],self.scene,100,100,1,'1', captured + timedelta(seconds=1))
        self.assertEqual(result['state'],'entry')

    def test_storage_failure_is_reported_without_throwing_into_ocr(self):
        with patch.object(self.store, "record", side_effect=OSError("test disk failure")):
            result = self.frame(.3)
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["visitIds"],[])

    def test_shared_camera_source_is_preview_only(self):
        self.status["sharedFrom"] = "A"
        self.assertEqual(self.frame(.3)["state"],"preview-only")
        self.assertEqual(self.store.list_visits()["total"],0)
