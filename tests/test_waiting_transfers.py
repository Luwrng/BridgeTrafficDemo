"""Giờ vào = lần thấy đầu, ghép ra theo từng biển. Chỉ DB/ảnh tạm."""
import base64
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from test_vehicle_records import RecordFixture
import test_record_management as management
from vehicle_records import PassageRecorder
from first_seen_policy import apply_first_seen_policy, OLD_WAITING_NOTE


class WaitingTransferCases:
    event = RecordFixture.event

    def queue_second(self):
        first=self.store.record(self.event('first'))
        waiting=self.store.record(self.event('second-waits','A','51F00651',1))
        return first,waiting

    def test_second_car_a_to_c_keeps_first_time_and_completes_without_review(self):
        _,waiting=self.queue_second()
        self.assertEqual(self.store.detail(waiting['visitIds'][0])['entered_at'],'2026-10-14T03:00:01.000+00:00')
        self.store.record(self.event('first-out','C',seconds=2))
        result=self.store.record(self.event('second-out','C','51F00651',3))
        self.assertEqual(result['state'],'exit')
        self.assertEqual(result['visitIds'],waiting['visitIds'])
        self.assertFalse(result['occupancy']['occupied'])
        visit=self.store.detail(waiting['visitIds'][0])
        self.assertEqual((visit['entry_camera'],visit['exit_camera'],visit['status']),('A','C','COMPLETE'))
        self.assertEqual(visit['entered_at'],'2026-10-14T03:00:01.000+00:00')
        self.assertEqual(visit['exited_at'],'2026-10-14T03:00:03.000+00:00')
        self.assertFalse(visit['needs_review']); self.assertIsNone(visit['issue'])
        self.assertEqual([p['direction'] for p in visit['passages']],['entry','exit'])
        self.assertEqual(self.store.list_visits()['closed_count'],2)
        self.assertEqual(self.store.record(self.event('third','B','51H12345',4))['state'],'entry')
        self.assertEqual(self.store.record(self.event('third-out','E','51H12345',5))['state'],'exit')
        self.assertFalse(self.store.occupancy()['occupied'])

    def test_repeated_a_after_d_free_never_overwrites_first_seen_time_or_photo(self):
        _,waiting=self.queue_second()
        original=self.store.detail(waiting['visitIds'][0])
        self.store.record(self.event('first-out','C',seconds=2))
        entered=self.store.record(self.event('second-in','A','51F00651',3))
        self.assertTrue(entered['occupancy']['occupied'])
        updated=self.store.detail(waiting['visitIds'][0])
        self.assertEqual(updated['entered_at'],original['entered_at'])
        self.assertEqual(updated['passages'],original['passages'])
        self.assertEqual(self.store.record(self.event('second-out','C','51F00651',4))['state'],'exit')
        self.assertEqual(len(self.store.detail(waiting['visitIds'][0])['passages']),2)

    def test_waiting_vehicle_exit_is_saved_without_releasing_another_vehicle_d(self):
        first,waiting=self.queue_second()
        result=self.store.record(self.event('second-at-c','C','51F00651',2))
        self.assertEqual(result['state'],'exit')
        self.assertEqual(result['occupancy']['vehicle']['id'],first['visitIds'][0])
        self.assertTrue(result['occupancy']['occupied'])
        self.assertEqual(self.store.detail(waiting['visitIds'][0])['exit_camera'],'C')
        self.assertEqual(self.store.record(self.event('first-out','B',seconds=3))['state'],'exit')
        self.assertFalse(self.store.occupancy()['occupied'])

    def test_older_frame_cannot_close_visit_and_exit_repeats_cannot_reenter(self):
        _,waiting=self.queue_second()
        self.assertEqual(self.store.record(self.event('old-c','C','51F00651',0))['state'],'waiting')
        self.assertIsNone(self.store.detail(waiting['visitIds'][0])['exited_at'])
        self.store.record(self.event('first-out','C',seconds=2))
        self.store.record(self.event('second-out','C','51F00651',3))
        for second in (4,6,9):
            self.assertEqual(self.store.record(self.event(str(second),'C','51F00651',second))['state'],'already-exited')
        self.assertEqual(len(self.store.detail(waiting['visitIds'][0])['passages']),2)
        self.assertFalse(self.store.occupancy()['occupied'])

    def test_hidden_waiting_visit_returns_to_report_when_exit_is_confirmed(self):
        _,waiting=self.queue_second()
        self.store.set_deleted(waiting['visitIds'][0],True)
        self.store.record(self.event('first-out','C',seconds=2))
        self.store.record(self.event('second-out','C','51F00651',3))
        self.assertIsNone(self.store.detail(waiting['visitIds'][0])['deleted_at'])
        self.assertEqual(self.store.list_visits(filters={'gate':'C','status':'COMPLETE'})['total'],2)
        self.assertEqual(self.store.list_visits()['review_count'],0)

    def test_failed_waiting_exit_rolls_back_files_and_can_retry(self):
        first,waiting=self.queue_second()
        before=self.store.detail(waiting['visitIds'][0]); files=set(self.store.images.iterdir())
        with patch('vehicle_records.os.fsync',side_effect=[None,OSError('isolated-disk-failure')]):
            with self.assertRaises(OSError): self.store.record(self.event('second-out','C','51F00651',3))
        self.assertEqual(self.store.detail(waiting['visitIds'][0]),before)
        self.assertEqual(set(self.store.images.iterdir()),files)
        self.assertEqual(self.store.occupancy()['vehicle']['id'],first['visitIds'][0])
        self.assertEqual(self.store.record(self.event('second-out','C','51F00651',3))['state'],'exit')

    def test_editing_waiting_notes_does_not_admit_vehicle_into_d(self):
        first,waiting=self.queue_second()
        before=self.store.detail(waiting['visitIds'][0])
        payload={k:before[k] for k in ('plate','entered_at','exited_at','entry_camera','exit_camera','notes','review_note')}
        payload.update(notes='Ghi chú xe chờ',reason='Bổ sung ghi chú xe chờ',expected_version=before['edit_version'])
        self.store.save_manual(payload,before['id'])
        self.assertEqual(self.store.detail(before['id'])['status'],'WAITING')
        self.assertEqual(self.store.occupancy()['vehicle']['id'],first['visitIds'][0])

    def test_ocr_adapter_two_trips_reuses_c_tracker_without_losing_second_exit(self):
        recorder=PassageRecorder(self.store)
        def observe(camera,number,plate):
            detection=dict(plate=plate,confirmed=True,evidenceCount=3,confidence=.9,
                           crop='data:image/jpeg;base64,'+base64.b64encode(self.crop).decode())
            return recorder.observe(camera_id=camera,source_key='source-'+camera,engine_session='session',detections=[detection],
                frame_id=str(number),media_time=number,captured_at=self.event('time',seconds=number)['occurred_at'],
                width=100,height=100,scene=self.scene,source_kind='live',source_name='Camera '+camera,real_source=True)
        self.assertEqual(observe('A',1,'51K73109')['state'],'entry')
        self.assertEqual(observe('A',2,'51F00651')['state'],'waiting')
        self.assertEqual(observe('C',3,'51K73109')['state'],'exit')
        self.assertEqual(observe('C',4,'51F00651')['state'],'exit')
        self.assertFalse(self.store.occupancy()['occupied'])
        self.assertEqual(self.store.list_visits()['total'],2)

    def legacy_missing_times(self):
        first,waiting=self.queue_second()
        self.store.record(self.event('first-out','C',seconds=2))
        self.store.record(self.event('second-out','C','51F00651',3))
        queued=self.store.record(self.event('third-in','B','51H12345',4))
        fourth=self.store.record(self.event('fourth-wait','E','51G54321',5))
        # Dữ liệu phiên bản cũ, chỉ trong DB UUID/tạm của bài kiểm thử.
        with self.store.connection() as conn:
            conn.execute("UPDATE visits SET entered_at=NULL,status='NEEDS_REVIEW',issue=?,review_note=? WHERE id=?",(OLD_WAITING_NOTE,OLD_WAITING_NOTE,waiting['visitIds'][0]))
            conn.execute('UPDATE visits SET entered_at=NULL WHERE id=?',(fourth['visitIds'][0],))
            for identifier in (waiting['visitIds'][0],fourth['visitIds'][0]):
                conn.execute("UPDATE passages SET direction='waiting' WHERE visit_id=? AND direction='entry'",(identifier,))
        return waiting['visitIds'][0],fourth['visitIds'][0],queued['visitIds'][0]

    def test_upgrade_backfills_from_real_evidence_keeps_images_exit_time_and_d(self):
        completed,waiting,owner=self.legacy_missing_times()
        before=self.store.detail(completed); files={p.name:p.read_bytes() for p in self.store.images.iterdir()}
        result=apply_first_seen_policy(self.store)
        self.assertEqual(result['changed'],2)
        self.assertEqual(result['occupancy']['vehicle']['id'],owner)
        after=self.store.detail(completed)
        self.assertEqual(after['entered_at'],'2026-10-14T03:00:01.000+00:00')
        self.assertEqual(after['exited_at'],before['exited_at'])
        self.assertEqual(after['status'],'COMPLETE'); self.assertFalse(after['needs_review'])
        self.assertEqual(after['changes'][0]['action'],'first-seen-policy')
        self.assertEqual(self.store.detail(waiting)['entered_at'],'2026-10-14T03:00:05.000+00:00')
        self.assertEqual(self.store.detail(waiting)['status'],'WAITING')
        self.assertEqual(files,{p.name:p.read_bytes() for p in self.store.images.iterdir()})
        self.assertEqual(apply_first_seen_policy(self.store)['changed'],0)

    def test_upgrade_skips_manually_edited_visit(self):
        completed,waiting,_=self.legacy_missing_times()
        with self.store.connection() as conn:
            conn.execute("UPDATE visits SET review_note='Ghi chú cũ do người dùng nhập' WHERE id=?",(completed,))
            conn.execute("UPDATE visits SET origin='manual' WHERE id=?",(waiting,))
        before=self.store.detail(completed)
        self.assertEqual(apply_first_seen_policy(self.store)['changed'],0)
        self.assertEqual(self.store.detail(completed),before)

    def test_upgrade_failure_rolls_back_times_status_and_evidence_classification(self):
        completed,waiting,_=self.legacy_missing_times()
        with self.store.connection() as conn:
            conn.execute('INSERT INTO record_changes(id,visit_id,action,created_at,reason,before_json,after_json) VALUES (?,?,?,?,?,?,?)',
                ('a'*32,completed,'test-audit',self.event('time')['occurred_at'],'Isolated rollback test','{}','{}'))
        before=[self.store.detail(identifier) for identifier in (completed,waiting)]
        # Cố tình trùng ID audit, gây lỗi SAU khi đã UPDATE hồ sơ/ảnh trong transaction.
        with patch('first_seen_policy.uuid.uuid4',return_value=SimpleNamespace(hex='a'*32)):
            with self.assertRaises(Exception): apply_first_seen_policy(self.store)
        self.assertEqual([self.store.detail(identifier) for identifier in (completed,waiting)],before)


class SQLiteWaitingTransferTests(WaitingTransferCases,RecordFixture):
    pass


@unittest.skipUnless(os.environ.get('CAMERA_SQLSERVER_TESTS')=='1','SQL Server integration opt-in')
class SqlWaitingTransferTests(WaitingTransferCases,unittest.TestCase):
    setUpClass=classmethod(management.SqlManagementTests.setUpClass.__func__)
    drop_database=classmethod(management.SqlManagementTests.drop_database.__func__)
    setUp=management.SqlManagementTests.setUp


if __name__=='__main__': unittest.main()
