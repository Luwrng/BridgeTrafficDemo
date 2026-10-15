"""Quản lý hồ sơ trên DB tạm; không tạo dữ liệu thử trong QuanLyXe."""
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from vehicle_records import VisitStore
from record_management import RecordConflict
from sqlserver_store import SqlServerVisitStore,open_connection
from setup_sqlserver import ensure_database


class ManagementCases:
    def payload(self,**changes):
        result = dict(request_id=uuid.uuid4().hex,plate='51K12345',entry_camera='A',exit_camera='B',
                      entered_at='2026-10-13T02:00:00+00:00',exited_at='2026-10-13T02:10:00+00:00',
                      notes='Bổ sung theo sổ bảo vệ',review_note='',reason='Đối chiếu sổ ghi cổng',attachments=[])
        result.update(changes); return result

    def image(self,direction='entry'):
        return dict(data=base64.b64encode(self.scene).decode(),direction=direction,notes='Ảnh do bảo vệ bổ sung')

    def event(self,key,camera='A',plate='51K12345',seconds=0):
        return dict(event_key=key,camera_id=camera,plate=plate,
                    occurred_at=(datetime(2026,10,14,2,tzinfo=timezone.utc)+timedelta(seconds=seconds)).isoformat(),
                    source_kind='live',media_time=seconds,confidence=.9,scene=self.scene,crop=self.crop)

    def edit(self,visit,**values):
        body={key:visit[key] for key in ('plate','entry_camera','exit_camera','entered_at','exited_at','notes','review_note')}
        body.update(expected_version=visit['edit_version'],reason='Kiểm tra và sửa hồ sơ',attachments=[])
        body.update(values); return self.store.save_manual(body,visit['id'])

    def test_manual_complete_audits_and_keeps_uploaded_images_separate_from_ocr(self):
        payload=self.payload(attachments=[self.image(),self.image('exit')])
        result=self.store.save_manual(payload); visit=self.store.detail(result['visitIds'][0])
        self.assertEqual((visit['status'],visit['origin']),('COMPLETE','manual'))
        self.assertEqual(visit['passages'],[]); self.assertEqual(len(visit['attachments']),2)
        self.assertFalse(visit['needs_review']); self.assertFalse(result['occupancy']['occupied'])
        self.assertEqual(visit['changes'][0]['before'],{})
        self.assertEqual(visit['changes'][0]['after']['notes'],payload['notes'])
        self.assertEqual(self.store.evidence(visit['attachments'][0]['file_name']).read_bytes(),self.scene)

    def test_retry_same_request_does_not_create_second_visit_or_photo(self):
        payload=self.payload(attachments=[self.image()])
        first=self.store.save_manual(payload); second=self.store.save_manual(payload)
        self.assertEqual(first['visitIds'],second['visitIds']); self.assertEqual(second['state'],'duplicate')
        self.assertEqual(self.store.list_visits()['total'],1)
        self.assertEqual(len(list(self.store.images.iterdir())),1)

    def test_manual_pending_entry_holds_d_and_other_camera_can_close_it(self):
        result=self.store.save_manual(self.payload(entered_at=self.event('t')['occurred_at'],exited_at=None,exit_camera=None))
        self.assertTrue(result['occupancy']['occupied'])
        with self.assertRaises(RecordConflict): self.store.save_manual(self.payload(plate='51F00651',exited_at=None,exit_camera=None))
        out=self.store.record(self.event('exit','C',seconds=10))
        self.assertEqual(out['visitIds'],result['visitIds']); self.assertFalse(out['occupancy']['occupied'])
        self.assertEqual(self.store.detail(result['visitIds'][0])['exit_camera'],'C')

    def test_exit_only_is_reported_missing_entry_and_never_occupies_d(self):
        result=self.store.save_manual(self.payload(entered_at=None,entry_camera=None))
        visit=self.store.detail(result['visitIds'][0])
        self.assertEqual(visit['status'],'NEEDS_REVIEW'); self.assertFalse(result['occupancy']['occupied'])
        self.assertIn('Thiếu thời gian vào',visit['review_reasons'])

    def test_plate_edit_retains_original_camera_images_and_before_after_audit(self):
        identifier=self.store.record(self.event('entry'))['visitIds'][0]
        before=self.store.detail(identifier)
        self.edit(before,plate='51K54321',notes='Đã xác minh biển số')
        after=self.store.detail(identifier)
        self.assertEqual(after['passages'],before['passages'])
        self.assertEqual(after['changes'][0]['before']['plate'],'51K12345')
        self.assertEqual(after['changes'][0]['after']['plate'],'51K54321')
        self.assertEqual(self.store.occupancy()['vehicle']['plate'],'51K54321')
        self.assertEqual(self.store.record(self.event('out','E','51K54321',3))['state'],'exit')

    def test_stale_editor_cannot_undo_a_camera_exit_or_other_edit(self):
        identifier=self.store.record(self.event('entry'))['visitIds'][0]
        old=self.store.detail(identifier)
        self.store.record(self.event('exit','B',seconds=10))
        with self.assertRaises(RecordConflict): self.edit(old,notes='Dữ liệu cũ')
        current=self.store.detail(identifier)
        self.edit(current,notes='Lần chỉnh sửa mới')
        with self.assertRaises(RecordConflict): self.edit(current,notes='Không được ghi đè')
        self.assertFalse(self.store.occupancy()['occupied'])

    def test_archived_inside_remains_in_inside_list_and_cannot_edit_until_restored(self):
        identifier=self.store.record(self.event('entry'))['visitIds'][0]
        self.store.set_deleted(identifier,True)
        self.assertEqual(self.store.list_visits()['total'],0)
        inside=self.store.list_inside()
        self.assertEqual(inside['total'],1); self.assertTrue(inside['items'][0]['deleted_at'])
        self.assertEqual(inside['items'][0]['entered_at'],self.event('t')['occurred_at'].replace('+00:00','.000+00:00'))
        with self.assertRaises(RecordConflict): self.edit(self.store.detail(identifier))

    def test_report_flags_missing_photos_and_explicit_note_then_clears_when_completed(self):
        identifier=self.store.save_manual(self.payload(review_note='Bảo vệ cần đối chiếu'))['visitIds'][0]
        visit=self.store.detail(identifier)
        self.assertCountEqual(visit['review_reasons'],['Bảo vệ cần đối chiếu','Thiếu ảnh vào','Thiếu ảnh ra'])
        self.assertEqual(self.store.list_visits(filters={'review':'1'})['total'],1)
        self.edit(visit,review_note='',attachments=[self.image(),self.image('exit')])
        self.assertEqual(self.store.list_visits(filters={'review':'1'})['total'],0)

    def test_normal_open_camera_visit_does_not_warn_just_because_not_exited_yet(self):
        self.store.record(self.event('entry'))
        self.assertEqual(self.store.list_visits()['review_count'],0)
        self.assertEqual(self.store.list_inside()['total'],1)

    def test_waiting_visit_can_be_completed_manually_after_d_is_free(self):
        self.store.record(self.event('entry'))
        identifier=self.store.record(self.event('wait','B','51F00651',1))['visitIds'][0]
        self.store.record(self.event('exit','C',seconds=2))
        visit=self.store.detail(identifier)
        self.edit(visit,entered_at=self.event('time',seconds=3)['occurred_at'])
        updated=self.store.detail(identifier)
        self.assertEqual(updated['status'],'INSIDE'); self.assertIsNone(updated['issue'])
        self.assertTrue(self.store.occupancy()['occupied'])

    def test_same_plate_same_entry_time_rejected_even_for_historical_closed_visit(self):
        self.store.save_manual(self.payload())
        with self.assertRaises(RecordConflict): self.store.save_manual(self.payload())
        self.assertEqual(self.store.list_visits()['total'],1)

    def test_partial_plate_gate_status_and_paging_filters_agree(self):
        self.store.save_manual(self.payload())
        self.store.save_manual(self.payload(plate='51F00651',entry_camera='C',exit_camera='E',entered_at='2026-10-12T02:00:00Z',exited_at='2026-10-12T03:00:00Z'))
        query=dict(plate='51-k',gate='B',entry_camera='A',exit_camera='B',status='COMPLETE')
        data=self.store.list_visits(filters=query,limit=1)
        self.assertEqual(data['total'],1); self.assertEqual(data['items'][0]['plate'],'51K12345')
        self.assertEqual(data['counts'],{'COMPLETE':1})
        self.assertEqual(self.store.list_visits(filters={'gate':'E'})['total'],1)
        self.assertEqual(self.store.list_visits(filters={'plate':'99999'})['total'],0)

    def test_vietnamese_day_month_and_exclusive_range_boundaries(self):
        self.store.save_manual(self.payload(entered_at='2026-10-13T16:59:58Z',exited_at='2026-10-13T16:59:59.999Z'))
        self.store.save_manual(self.payload(plate='51F00651',entered_at='2026-10-13T17:00:00Z',exited_at='2026-10-13T17:00:01Z'))
        data=self.store.list_visits(filters={'day':'2026-10-14'})
        self.assertEqual(data['total'],1); self.assertEqual(data['items'][0]['plate'],'51F00651')
        self.assertEqual(self.store.list_visits(filters={'month':'2026-10'})['total'],2)
        self.assertEqual(self.store.list_visits(filters={'month':'2026-09'})['total'],0)
        bounds={'from':'2026-10-13T16:59:58Z','to':'2026-10-13T17:00:00Z','date_basis':'entry'}
        self.assertEqual(self.store.list_visits(filters=bounds)['total'],1)

    def test_invalid_filters_and_future_times_do_not_write(self):
        for filters in [{'plate':"%' OR 1=1--"},{'gate':'Z'},{'month':'2026-13'},{'day':'2026-02-30'},
                        {'from':'2026-10-13T01:00:00'},{'review':'yes'},{'status':'ALL'},
                        {'from':'2026-10-14T01:00:00Z','to':'2026-10-13T01:00:00Z'}]:
            with self.subTest(filters=filters),self.assertRaises(ValueError): self.store.list_visits(filters=filters)
        future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        for values in [{'entered_at':future},{'plate':'not-a-plate'},{'reason':''},{'exit_camera':'Z'},
                       {'entered_at':'2026-10-13T05:00:00Z'},{'attachments':[{'data':'no','direction':'entry'}]}]:
            with self.subTest(values=values),self.assertRaises(ValueError): self.store.save_manual(self.payload(**values))
        self.assertEqual(self.store.list_visits()['total'],0); self.assertEqual(list(self.store.images.iterdir()),[])

    def test_failed_attachment_transaction_keeps_visit_audit_and_old_images(self):
        identifier=self.store.save_manual(self.payload(attachments=[self.image()]))['visitIds'][0]
        before=self.store.detail(identifier); files=set(self.store.images.iterdir())
        def drop_trigger():
            with self.store.connection() as conn: conn.execute('DROP TRIGGER IF EXISTS fail_attachment')
        self.addCleanup(drop_trigger)
        with self.store.connection() as conn:
            if self.store.storage_info()['backend']=='sqlserver':
                conn.execute("CREATE TRIGGER fail_attachment ON visit_attachments AFTER INSERT AS BEGIN THROW 51002, 'isolated-test-failure', 1; END")
            else:
                conn.execute("CREATE TRIGGER fail_attachment BEFORE INSERT ON visit_attachments BEGIN SELECT RAISE(ABORT,'isolated-test-failure'); END")
        with self.assertRaises(Exception) as error: self.edit(before,notes='Không được lưu khi ảnh lỗi',attachments=[self.image('exit')])
        self.assertIn('isolated-test-failure',str(error.exception))
        self.assertEqual(self.store.detail(identifier),before)
        self.assertEqual(set(self.store.images.iterdir()),files)


class SQLiteManagementTests(ManagementCases,unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=VisitStore(self.temp.name)
        self.scene=(ROOT/'artifacts/ui-check/camera-live.jpg').read_bytes()
        self.crop=(ROOT/'artifacts/ui-check/plate-crop.jpg').read_bytes()


@unittest.skipUnless(os.environ.get('CAMERA_SQLSERVER_TESTS')=='1','SQL Server integration opt-in')
class SqlManagementTests(ManagementCases,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database='QuanLyXe_Test_'+uuid.uuid4().hex
        cls.settings=dict(backend='sqlserver',server=r'.\SQLEXPRESS',database=cls.database,trustServerCertificate=True)
        ensure_database(cls.settings); cls.addClassCleanup(cls.drop_database)

    @classmethod
    def drop_database(cls):
        if not re.fullmatch('QuanLyXe_Test_[0-9a-f]{32}',cls.database): raise RuntimeError('Sai database tạm.')
        raw=open_connection(cls.settings,'master')
        try:
            raw.execute(f'ALTER DATABASE [{cls.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE')
            raw.execute(f'DROP DATABASE [{cls.database}]')
        finally: raw.close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=SqlServerVisitStore(self.settings,self.temp.name)
        with self.store.connection() as conn:
            for table in ('record_changes','visit_attachments','visit_corrections','passages','visits','plate_release','migration_runs'):
                conn.execute('DELETE FROM '+table)
            conn.execute('UPDATE gate_state SET revision=0 WHERE id=1')
        self.scene=(ROOT/'artifacts/ui-check/camera-live.jpg').read_bytes()
        self.crop=(ROOT/'artifacts/ui-check/plate-crop.jpg').read_bytes()


if __name__=='__main__': unittest.main()
