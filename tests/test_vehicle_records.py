"""Kiểm thử luồng cổng/D và bằng chứng; tất cả ghi vào DB tạm, không vào app."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from vehicle_records import VisitStore, PassageRecorder
from vehicle_record_server import RecordHandler, RecordHTTPServer


class RecordFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = VisitStore(self.temp.name)
        # Ảnh thật đã chụp từ RTSP, chỉ dùng làm fixture kiểm tra lưu file.
        self.scene = (ROOT / "artifacts/ui-check/camera-live.jpg").read_bytes()
        self.crop = (ROOT / "artifacts/ui-check/plate-crop.jpg").read_bytes()

    def event(self, key, camera="A", plate="51K73109", seconds=0, **extra):
        at = datetime(2026,10,14,3,tzinfo=timezone.utc) + timedelta(seconds=seconds)
        return dict(event_key=key, plate=plate, camera_id=camera, occurred_at=at.isoformat(),
                    source_kind="live", media_time=seconds, confidence=.9, scene=self.scene, crop=self.crop, **extra)


class RecordTests(RecordFixture):
    def test_current_inside_visit_stays_first_when_new_waiting_plates_arrive(self):
        self.store.record(self.event('inside'))
        self.store.record(self.event('wait','B','51F00651',seconds=10))
        self.assertEqual(self.store.list_visits()['items'][0]['status'],'INSIDE')

    def test_archived_inside_is_visible_in_consistent_list_snapshot(self):
        entered = self.store.record(self.event('in'))
        self.store.set_deleted(entered['visitIds'][0], True)
        snapshot = self.store.list_visits()
        self.assertEqual(snapshot['total'], 0)
        self.assertTrue(snapshot['occupancy']['occupied'])
        self.assertTrue(snapshot['occupancy']['vehicle']['deleted_at'])
        self.assertEqual(snapshot['occupancy']['vehicle']['plate'], '51K73109')

    def test_new_entry_restores_archived_waiting_visit(self):
        self.store.record(self.event('in'))
        waiting = self.store.record(self.event('wait','B','51F00651', seconds=1))
        self.store.set_deleted(waiting['visitIds'][0], True)
        self.store.record(self.event('out','C',seconds=2))
        result = self.store.record(self.event('next','B','51F00651',seconds=3))
        self.assertEqual(result['state'], 'entry')
        self.assertEqual(result['visitIds'], waiting['visitIds'])
        self.assertIsNone(self.store.detail(result['visitIds'][0])['deleted_at'])
        self.assertEqual(self.store.list_visits()['counts']['INSIDE'], 1)

    def test_manual_exit_records_audit_without_inventing_camera_evidence(self):
        visit_id = self.store.record(self.event('in'))['visitIds'][0]
        self.store.set_deleted(visit_id, True)
        result = self.store.confirm_exit(visit_id,'B',self.event('time',seconds=60)['occurred_at'],'Đã kiểm tra xe rời D')
        self.assertFalse(result['occupancy']['occupied'])
        visit = self.store.detail(visit_id)
        self.assertEqual(visit['status'], 'COMPLETE')
        self.assertEqual(visit['exit_camera'], 'B')
        self.assertEqual(len(visit['passages']), 1)
        self.assertEqual(len(visit['corrections']), 1)
        self.assertIsNotNone(visit['deleted_at'])
        self.assertIn('thủ công', visit['issue'])
        with self.assertRaises(ValueError):
            self.store.confirm_exit(visit_id,'C',self.event('time',seconds=61)['occurred_at'],'Thử xác nhận lặp')
        new = self.store.record(self.event('next','E','51F00651',seconds=62))
        self.assertEqual(new['state'], 'entry')

    def test_manual_exit_rejects_invalid_times_gate_reason_without_releasing_d(self):
        visit_id = self.store.record(self.event('in',seconds=10))['visitIds'][0]
        for camera, seconds, reason in [('Z',11,'Đã kiểm tra'),('B',9,'Đã kiểm tra'),('B',11,'')]:
            with self.assertRaises(ValueError):
                self.store.confirm_exit(visit_id,camera,self.event('time',seconds=seconds)['occurred_at'],reason)
            self.assertTrue(self.store.occupancy()['occupied'])
        self.assertEqual(len(self.store.detail(visit_id)['corrections']), 0)

    def test_first_confirmed_plate_enters_and_other_camera_exits(self):
        entry = self.store.record(self.event("entry", "C"))
        self.assertEqual(entry["state"], "entry")
        self.assertTrue(entry["occupancy"]["occupied"])
        result = self.store.record(self.event("exit", "E", seconds=60))
        self.assertEqual(result["state"], "exit")
        self.assertFalse(result["occupancy"]["occupied"])
        visit = VisitStore(self.temp.name).detail(entry["visitIds"][0])
        self.assertEqual(visit["status"], "COMPLETE")
        self.assertEqual((visit["entry_camera"],visit["exit_camera"]), ("C","E"))
        self.assertEqual(visit["entered_at"], "2026-10-14T03:00:00.000+00:00")
        self.assertEqual(visit["exited_at"], "2026-10-14T03:01:00.000+00:00")
        self.assertEqual(len(visit["passages"]), 2)

    def test_same_camera_repeats_do_not_close_or_create_another_visit(self):
        self.store.record(self.event("first"))
        for seconds in (1,2,10,30):
            self.assertEqual(self.store.record(self.event(str(seconds),seconds=seconds))["state"], "already-inside")
        self.assertEqual(self.store.list_visits()["total"], 1)
        self.assertEqual(len(list(self.store.images.iterdir())), 2)
        self.assertTrue(VisitStore(self.temp.name).occupancy()["occupied"])

    def test_different_plate_waits_without_clearing_d_or_inventing_entry_time(self):
        self.store.record(self.event("inside"))
        waiting = self.store.record(self.event("wait", "B", "51F00651", seconds=2))
        self.assertEqual(waiting["state"], "waiting")
        visit = self.store.detail(waiting["visitIds"][0])
        self.assertEqual(visit["status"], "WAITING")
        self.assertIsNone(visit["entered_at"])
        self.assertTrue(self.store.occupancy()["occupied"])
        self.assertEqual(self.store.occupancy()["vehicle"]["plate"], "51K73109")
        self.store.record(self.event("wait-repeat", "E", "51F00651", seconds=3))
        self.assertEqual(self.store.list_visits()["total"], 2)

    def test_waiting_vehicle_only_enters_after_fresh_observation_when_d_clears(self):
        self.store.record(self.event("inside"))
        waiting = self.store.record(self.event("wait", "B", "51F00651", seconds=2))
        self.store.record(self.event("exit", "C", seconds=4))
        self.assertFalse(self.store.occupancy()["occupied"])
        stale = self.store.record(self.event("stale-wait", "B", "51F00651", seconds=3))
        self.assertEqual(stale["state"], "stale-frame")
        result = self.store.record(self.event("new-wait", "B", "51F00651", seconds=5))
        self.assertEqual(result["visitIds"], waiting["visitIds"])
        visit = self.store.detail(result["visitIds"][0])
        self.assertEqual(visit["entered_at"], "2026-10-14T03:00:05.000+00:00")
        self.assertEqual(len(visit["passages"]),2)
        self.assertEqual(visit["status"], "INSIDE")

    def test_other_camera_older_or_equal_frame_cannot_release_d(self):
        self.store.record(self.event("entry",seconds=10))
        for seconds in (9,10):
            self.assertEqual(self.store.record(self.event(str(seconds),"B",seconds=seconds))["state"], "already-inside")
        self.assertTrue(self.store.occupancy()["occupied"])

    def test_other_gate_with_same_rtsp_source_closes_the_original_visit(self):
        self.store.record(self.event("entry",source_fingerprint="a"*64))
        result = self.store.record(self.event("same-feed","B",seconds=1,source_fingerprint="a"*64))
        self.assertEqual(result["state"], "exit")
        self.assertFalse(result['occupancy']['occupied'])
        self.assertEqual(self.store.list_visits()['total'],1)
        visit = self.store.detail(result['visitIds'][0])
        self.assertEqual(visit['exit_camera'],'B')
        self.assertEqual(len(visit['passages']),2)

    def test_visible_plate_after_exit_does_not_reenter_even_after_restart(self):
        self.store.record(self.event("entry"))
        self.store.record(self.event("exit","B",seconds=1))
        store = VisitStore(self.temp.name)
        for seconds in (2,5,8,11):
            self.assertEqual(store.record(self.event(str(seconds),"B",seconds=seconds))["state"], "already-exited")
        self.assertFalse(store.occupancy()["occupied"])
        self.assertEqual(store.record(self.event("after-absence",seconds=17))["state"], "entry")
        self.assertEqual(store.list_visits()["total"],2)

    def test_same_event_retry_is_idempotent_with_no_extra_images(self):
        event = self.event("retry")
        self.store.record(event)
        self.assertEqual(self.store.record(event)["state"], "duplicate")
        self.assertEqual(self.store.list_visits()["total"],1)
        self.assertEqual(len(list(self.store.images.iterdir())),2)

    def test_evidence_keeps_real_scene_crop_and_checksums(self):
        visit = self.store.detail(self.store.record(self.event("images"))["visitIds"][0])
        passage = visit["passages"][0]
        self.assertEqual(self.store.evidence(passage["scene_file"]).read_bytes(),self.scene)
        self.assertEqual(self.store.evidence(passage["crop_file"]).read_bytes(),self.crop)
        self.assertEqual(passage["scene_sha256"],hashlib.sha256(self.scene).hexdigest())
        self.assertEqual(passage["crop_sha256"],hashlib.sha256(self.crop).hexdigest())
        self.assertIsNone(self.store.evidence("../visits.sqlite3"))
        self.assertIsNone(self.store.evidence("0"*32 + "_scene.jpg"))

    def test_failed_second_image_write_rolls_back_files_visit_and_d(self):
        with patch("vehicle_records.os.fsync",side_effect=[None,OSError("test disk failure")]):
            with self.assertRaises(OSError): self.store.record(self.event("bad-disk"))
        self.assertEqual(self.store.list_visits()["total"],0)
        self.assertFalse(self.store.occupancy()["occupied"])
        self.assertEqual(list(self.store.images.iterdir()),[])

    def test_simultaneous_cameras_can_only_admit_one_vehicle(self):
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.store.record,[self.event("one"),self.event("two","B","51F00651")]))
        self.assertEqual({item["state"] for item in results},{"entry","waiting"})
        self.assertEqual(self.store.occupancy()["waitingCount"],1)
        self.assertEqual(sum(row["status"] == "INSIDE" for row in self.store.list_visits()["items"]),1)

    def test_invalid_plate_file_source_and_confidence_do_not_create_visits(self):
        for change in ({"plate":"guess"},{"source_kind":"file"},{"confidence":float("nan")},{"confidence":.6}):
            event = self.event("invalid"); event.update(change)
            with self.assertRaises(ValueError): self.store.record(event)
        self.assertEqual(self.store.list_visits()["total"],0)

    def test_legacy_multiple_open_visits_keep_d_locked_and_require_review(self):
        self.store.record(self.event("one"))
        with self.store.connection() as conn:
            conn.execute("INSERT INTO visits(id,realm,plate,entered_at,entry_camera,status,created_at) VALUES ('old','real','51F00651','2026-10-14T03:00:00.000+00:00','B','INSIDE','2026-10-14T03:00:00.000+00:00')")
        self.assertEqual(self.store.record(self.event("ambiguous","B",seconds=10))["state"], "needs-review")
        self.assertTrue(self.store.occupancy()["occupied"])
        self.assertIsNotNone(self.store.occupancy()["issue"])

    def test_trash_and_restore_keep_real_evidence_and_times_after_restart(self):
        visit_id = self.store.record(self.event("entry"))["visitIds"][0]
        self.store.record(self.event("exit","B",seconds=10))
        before = self.store.detail(visit_id)
        self.assertEqual(self.store.set_deleted(visit_id,True)["state"],"trashed")
        self.assertEqual(self.store.list_visits()["total"],0)
        restored_store = VisitStore(self.temp.name)
        self.assertEqual(restored_store.list_visits(deleted=True)["total"],1)
        self.assertEqual(restored_store.set_deleted(visit_id,False)["state"],"restored")
        after = restored_store.detail(visit_id)
        for field in ("plate","entered_at","exited_at","entry_camera","exit_camera","passages"):
            self.assertEqual(before[field],after[field])
        for passage in after["passages"]:
            self.assertEqual(restored_store.evidence(passage["scene_file"]).read_bytes(),self.scene)
            self.assertEqual(restored_store.evidence(passage["crop_file"]).read_bytes(),self.crop)

    def test_trashing_inside_does_not_release_d_and_other_camera_still_records_exit(self):
        visit_id = self.store.record(self.event("entry"))["visitIds"][0]
        self.store.set_deleted(visit_id,True)
        self.assertTrue(self.store.occupancy()["occupied"])
        self.assertEqual(self.store.occupancy()["vehicle"]["id"],visit_id)
        result = self.store.record(self.event("exit","B",seconds=1))
        self.assertFalse(result["occupancy"]["occupied"])
        self.assertEqual(result["occupancy"]["lastExit"]["exit_camera"],"B")
        self.assertEqual(result["occupancy"]["lastExit"]["entry_camera"],"A")
        self.assertEqual(self.store.list_visits()["total"],0)
        self.assertEqual(self.store.list_visits(deleted=True)["items"][0]["status"],"COMPLETE")
        self.store.set_deleted(visit_id,False)
        self.assertEqual(self.store.detail(visit_id)["exit_camera"],"B")

    def test_delete_retry_is_idempotent_and_missing_id_changes_nothing(self):
        visit_id = self.store.record(self.event("entry"))["visitIds"][0]
        first = self.store.set_deleted(visit_id,True)
        self.assertEqual(self.store.set_deleted(visit_id,True),first)
        self.assertIsNone(self.store.set_deleted("0"*32,True))
        self.assertEqual(self.store.list_visits(deleted=True)["total"],1)

    def test_archive_failure_rolls_back_without_deleting_images(self):
        visit_id = self.store.record(self.event("entry"))["visitIds"][0]
        with self.store.connection() as conn: conn.execute("DROP TABLE gate_state")
        with self.assertRaises(sqlite3.OperationalError): self.store.set_deleted(visit_id,True)
        # Danh sách mới chứa cả snapshot D; DB hỏng phải báo lỗi thay vì
        # trả trạng thái xanh. Kiểm tra trực tiếp transaction đã rollback.
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM visits WHERE deleted_at IS NULL").fetchone()[0],1)
        with self.assertRaises(sqlite3.OperationalError): self.store.list_visits()
        self.assertIsNone(self.store.detail(visit_id)["deleted_at"])
        self.assertEqual(len(list(self.store.images.iterdir())),2)


class RecorderTests(RecordFixture):
    def setUp(self):
        super().setUp(); self.recorder = PassageRecorder(self.store)

    def frame(self,camera="A",identifier=1,**changes):
        detection = dict(trackId=1,plate="51K73109",confirmed=True,evidenceCount=3,confidence=.9,
                         crop="data:image/jpeg;base64,"+base64.b64encode(self.crop).decode())
        detection.update(changes.pop("detection",{}))
        return self.recorder.observe(camera_id=camera,source_key="unit-source",engine_session="unit-session",detections=[detection],
            frame_id=str(identifier),media_time=identifier,captured_at=self.event("time",seconds=identifier)["occurred_at"],
            width=100,height=100,scene=self.scene,source_kind=changes.pop("source_kind","live"),source_name="ignored",real_source=True,**changes)

    def test_stationary_plate_is_recorded_without_direction_or_line(self):
        self.assertEqual(self.frame()["state"],"entry")

    def test_uncertain_ocr_or_changed_consensus_does_not_change_d(self):
        for identifier, values in enumerate(({"confirmed":False},{"evidenceCount":2},{"confidence":.6},{"changedConfirmed":True}),1):
            self.assertEqual(self.frame(identifier=identifier,detection=values)["state"],"waiting-confirmation")
        self.assertFalse(self.store.occupancy()["occupied"])

    def test_file_is_preview_only(self):
        self.assertEqual(self.frame(source_kind="file")["state"],"preview-only")
        self.assertEqual(self.store.list_visits()["total"],0)

    def test_duplicate_and_out_of_order_frames_do_not_record(self):
        self.frame(identifier=2)
        self.assertEqual(self.frame(identifier=2)["state"],"duplicate-frame")
        self.assertEqual(self.frame(identifier=1)["state"],"duplicate-frame")

    def test_other_camera_confirmation_closes_the_same_visit(self):
        entry = self.frame()
        self.assertEqual(self.frame("B",2)["visitIds"],entry["visitIds"])
        self.assertFalse(self.store.occupancy()["occupied"])

    def test_failed_record_can_retry_same_frame(self):
        with patch.object(self.store,"record",side_effect=OSError("disk")):
            with self.assertRaises(OSError): self.frame()
        self.assertEqual(self.frame()["state"],"entry")


class HTTPTests(RecordFixture):
    def setUp(self):
        super().setUp()
        handler = type("IsolatedRecordHandler",(RecordHandler,),{"store":self.store})
        self.server = RecordHTTPServer(("127.0.0.1",0),handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def test_status_occupancy_and_cors(self):
        with urlopen(Request(self.base+"/api/occupancy",headers={"Origin":"http://localhost:5000"})) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"],"http://localhost:5000")
            self.assertFalse(json.load(response)["occupied"])
        with urlopen(Request(self.base+"/api/visits",headers={"Origin":"http://example.invalid"})) as response:
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_http_manual_exit_requires_confirmation_and_returns_same_snapshot(self):
        visit_id = self.store.record(self.event('in'))['visitIds'][0]
        path = self.base + f'/api/visits/{visit_id}/confirm-exit'
        payload = {'camera':'E','occurredAt':self.event('time',seconds=60)['occurred_at'],'reason':'Đã kiểm tra xe ra'}
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}))
        self.assertEqual(error.exception.code,400)
        payload['confirm'] = True
        with urlopen(Request(path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})) as response:
            self.assertFalse(json.load(response)['occupancy']['occupied'])
        with urlopen(self.base+'/api/visits') as response:
            snapshot = json.load(response)
            self.assertFalse(snapshot['occupancy']['occupied'])
            self.assertEqual(snapshot['items'][0]['exit_camera'],'E')

    def test_read_visits_evidence_and_no_source_credentials(self):
        result = self.store.record(self.event("http"))
        with urlopen(self.base+"/api/visits/"+result["visitIds"][0]) as response: visit=json.load(response)
        with urlopen(self.base+"/evidence/"+visit["passages"][0]["scene_file"]) as response:
            self.assertEqual(response.read(),self.scene)
        self.assertNotIn("rtsp://",json.dumps(visit))

    def test_cannot_create_edit_visits_or_camera_direction_from_ui(self):
        for path in ("/api/visits","/api/camera-rules/A"):
            with self.assertRaises(HTTPError) as error: urlopen(Request(self.base+path,data=b"{}"))
            self.assertEqual(error.exception.code,405)
        with self.assertRaises(HTTPError) as error: urlopen(self.base+"/api/camera-rules")
        self.assertEqual(error.exception.code,404)

    def test_rejects_removed_realm_and_bad_paging_and_unknown_photo(self):
        for path,code in (("/api/visits?realm=demo",400),("/api/visits?limit=0",400),("/evidence/"+"0"*32+"_scene.jpg",404)):
            with self.assertRaises(HTTPError) as error: urlopen(self.base+path)
            self.assertEqual(error.exception.code,code)

    def test_http_trash_restore_requires_confirmation_and_keeps_d(self):
        visit_id = self.store.record(self.event("http-entry"))["visitIds"][0]
        def action(name,body=b'{"confirm":true}',origin="http://127.0.0.1:5080"):
            return urlopen(Request(self.base+f"/api/visits/{visit_id}/{name}",data=body,headers={"Content-Type":"application/json","Origin":origin}))
        for body,origin,code in ((b'{}',"http://127.0.0.1:5080",400),(b'{"confirm":true}',"http://example.invalid",403)):
            with self.assertRaises(HTTPError) as error: action("trash",body,origin)
            self.assertEqual(error.exception.code,code)
        with action("trash") as response: self.assertTrue(json.load(response)["occupancy"]["occupied"])
        with urlopen(self.base+"/api/visits?deleted=1") as response: self.assertEqual(json.load(response)["total"],1)
        with action("restore") as response: self.assertEqual(json.load(response)["state"],"restored")
        with urlopen(self.base+"/api/visits") as response: self.assertEqual(json.load(response)["total"],1)

    def test_http_missing_visit_or_invalid_deleted_filter(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base+"/api/visits/"+"0"*32+"/trash",data=b'{"confirm":true}',headers={"Content-Type":"application/json"}))
        self.assertEqual(error.exception.code,404)
        with self.assertRaises(HTTPError) as error: urlopen(self.base+"/api/visits?deleted=anything")
        self.assertEqual(error.exception.code,400)

    def test_http_manual_create_edit_attachment_and_search(self):
        payload=dict(confirm=True,request_id=uuid.uuid4().hex,plate='51K12345',entry_camera='A',exit_camera='B',
                     entered_at='2026-10-13T02:00:00Z',exited_at='2026-10-13T03:00:00Z',
                     notes='Bổ sung theo sổ',review_note='',reason='Đối chiếu sổ bảo vệ',
                     attachments=[dict(data=base64.b64encode(self.scene).decode(),direction='entry')])
        def post(path,body,origin='http://127.0.0.1:5080'):
            return urlopen(Request(self.base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':origin}))
        with self.assertRaises(HTTPError) as error: post('/api/visits/manual',payload,'http://example.invalid')
        self.assertEqual(error.exception.code,403)
        with post('/api/visits/manual',payload) as response: identifier=json.load(response)['visitIds'][0]
        visit=self.store.detail(identifier)
        with urlopen(self.base+'/evidence/'+visit['attachments'][0]['file_name']) as response:
            self.assertEqual(response.headers['Content-Type'],'image/jpeg'); self.assertEqual(response.read(),self.scene)
        payload.update(expected_version=visit['edit_version'],plate='51K54321',attachments=[])
        with post('/api/visits/'+identifier+'/edit',payload) as response: self.assertEqual(json.load(response)['state'],'manual-edited')
        with self.assertRaises(HTTPError) as error: post('/api/visits/'+identifier+'/edit',payload)
        self.assertEqual(error.exception.code,409)
        with urlopen(self.base+'/api/visits?plate=543&gate=B&month=2026-10&review=1') as response:
            self.assertEqual(json.load(response)['total'],1)  # Chưa bổ sung ảnh ra.
        with urlopen(self.base+'/api/inside') as response: self.assertEqual(json.load(response)['total'],0)

    def test_http_new_manual_endpoint_requires_confirmation_and_valid_filter(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base+'/api/visits/manual',data=b'{}',headers={'Content-Type':'application/json'}))
        self.assertEqual(error.exception.code,400)
        for query in ('plate=x%25','month=2026-13','status=anything','gate=A&gate=B'):
            with self.assertRaises(HTTPError) as error: urlopen(self.base+'/api/visits?'+query)
            self.assertEqual(error.exception.code,400)


class ExclusiveServerTests(unittest.TestCase):
    def test_two_record_servers_cannot_share_one_port(self):
        first=RecordHTTPServer(("127.0.0.1",0),RecordHandler); self.addCleanup(first.server_close)
        with self.assertRaises(OSError): RecordHTTPServer(first.server_address,RecordHandler)


if __name__ == "__main__":
    unittest.main()
