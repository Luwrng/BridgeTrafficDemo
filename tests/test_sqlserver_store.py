"""Integration trên SQL Server thật, DB riêng QuanLyXe_Test_<UUID>.

Bật CAMERA_SQLSERVER_TESTS=1. Không ghi/sửa/xóa dữ liệu DB QuanLyXe.
Chỉ DB có đúng tên UUID do suite này tạo mới được dọn sau kiểm thử.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from setup_sqlserver import ensure_database,migrate
from sqlserver_store import SqlServerVisitStore,open_connection,driver,connection_string
from vehicle_records import VisitStore
from record_storage import create_visit_store


@unittest.skipUnless(os.environ.get('CAMERA_SQLSERVER_TESTS') == '1','SQL Server integration opt-in')
class SqlServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = 'QuanLyXe_Test_' + uuid.uuid4().hex
        cls.settings = {'backend':'sqlserver','server':r'.\SQLEXPRESS','database':cls.database,'trustServerCertificate':True}
        ensure_database(cls.settings)
        cls.addClassCleanup(cls.drop_test_database)

    @classmethod
    def drop_test_database(cls):
        if not re.fullmatch('QuanLyXe_Test_[0-9a-f]{32}',cls.database): raise RuntimeError('Không đúng DB tạm.')
        raw = open_connection(cls.settings,'master')
        try:
            raw.execute(f'ALTER DATABASE [{cls.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE')
            raw.execute(f'DROP DATABASE [{cls.database}]')
        finally: raw.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = SqlServerVisitStore(self.settings,self.temp.name)
        with self.store.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DROP TRIGGER IF EXISTS dbo.fail_passage')
            for table in ['record_changes','visit_attachments','visit_corrections','passages','visits','plate_release','migration_runs']:
                conn.execute(f'DELETE FROM {table}')
            conn.execute('UPDATE gate_state SET revision=0 WHERE id=1')
        self.scene = (ROOT/'artifacts/ui-check/camera-live.jpg').read_bytes()
        self.crop = (ROOT/'artifacts/ui-check/plate-crop.jpg').read_bytes()

    def event(self,key,camera='A',plate='51K73109',seconds=0,**extra):
        return dict(event_key=key,camera_id=camera,plate=plate,occurred_at=(datetime(2026,10,14,3,tzinfo=timezone.utc)+timedelta(seconds=seconds)).isoformat(),
                    source_kind='live',media_time=seconds,confidence=.9,scene=self.scene,crop=self.crop,**extra)

    def test_entry_wait_exit_next_vehicle_and_utc_dates(self):
        entered = self.store.record(self.event('in','C'))
        waiting = self.store.record(self.event('wait','A','51F00651',1))
        self.assertEqual(waiting['state'],'waiting')
        self.assertIsNone(self.store.detail(waiting['visitIds'][0])['entered_at'])
        self.assertEqual(self.store.record(self.event('same','C',seconds=2))['state'],'already-inside')
        exited = self.store.record(self.event('out','E',seconds=3))
        self.assertFalse(exited['occupancy']['occupied'])
        visit = self.store.detail(entered['visitIds'][0])
        self.assertEqual((visit['entry_camera'],visit['exit_camera']),('C','E'))
        self.assertEqual(visit['entered_at'],'2026-10-14T03:00:00.000+00:00')
        self.assertEqual(visit['exited_at'],'2026-10-14T03:00:03.000+00:00')
        self.assertEqual(self.store.record(self.event('stale','A','51F00651',2))['state'],'stale-frame')
        self.assertEqual(self.store.record(self.event('next','A','51F00651',4))['state'],'entry')
        self.assertEqual(self.store.list_visits()['counts']['INSIDE'],1)

    def test_archive_exit_restore_evidence_and_restart(self):
        visit_id = self.store.record(self.event('in'))['visitIds'][0]
        self.store.set_deleted(visit_id,True)
        self.assertEqual(self.store.list_visits()['total'],0)
        self.assertTrue(self.store.list_visits()['occupancy']['vehicle']['deleted_at'])
        self.store.record(self.event('out','B',seconds=5))
        store = SqlServerVisitStore(self.settings,self.temp.name)
        self.assertFalse(store.occupancy()['occupied'])
        store.set_deleted(visit_id,False)
        visit = store.detail(visit_id)
        self.assertEqual(visit['status'],'COMPLETE')
        for passage in visit['passages']:
            self.assertEqual(hashlib.sha256(store.evidence(passage['scene_file']).read_bytes()).hexdigest(),passage['scene_sha256'])

    def test_gate_id_decides_exit_even_if_rtsp_is_same_and_duplicates_stay_suppressed(self):
        self.store.record(self.event('in',source_fingerprint='a'*64))
        self.assertEqual(self.store.record(self.event('still-a','A',seconds=1,source_fingerprint='a'*64))['state'],'already-inside')
        result = self.store.record(self.event('out','B',seconds=2,source_fingerprint='a'*64))
        self.assertEqual(result['state'],'exit')
        self.assertEqual(self.store.detail(result['visitIds'][0])['exit_camera'],'B')
        self.assertEqual(len(self.store.detail(result['visitIds'][0])['passages']),2)
        restarted = SqlServerVisitStore(self.settings,self.temp.name)
        for second in [3,7,10]:
            self.assertEqual(restarted.record(self.event(str(second),'B',seconds=second))['state'],'already-exited')
        self.assertEqual(restarted.list_visits()['total'],1)

    def test_unicode_manual_exit_and_no_fabricated_exit_photo(self):
        visit_id = self.store.record(self.event('in'))['visitIds'][0]
        result = self.store.confirm_exit(visit_id,'C',self.event('time',seconds=60)['occurred_at'],'Đã kiểm tra: xe rời D, camera bỏ lỡ')
        self.assertFalse(result['occupancy']['occupied'])
        visit = self.store.detail(visit_id)
        self.assertEqual(visit['corrections'][0]['reason'],'Đã kiểm tra: xe rời D, camera bỏ lỡ')
        self.assertEqual(len(visit['passages']),1)
        with self.assertRaises(ValueError): self.store.confirm_exit(visit_id,'B',self.event('t',seconds=61)['occurred_at'],'Lần xác nhận lặp')

    def test_simultaneous_cameras_only_admit_one_vehicle(self):
        barrier = threading.Barrier(2)
        def record(event): barrier.wait(); return self.store.record(event)['state']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(record,[self.event('a'),self.event('b','B','51F00651')]))
        self.assertCountEqual(results,['entry','waiting'])
        self.assertEqual(self.store.list_visits()['counts']['INSIDE'],1)

    def test_duplicate_requests_do_not_write_extra_images(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.store.record,[self.event('same'),self.event('same')]))
        self.assertCountEqual([item['state'] for item in results],['entry','duplicate'])
        self.assertEqual(len(list(self.store.images.iterdir())),2)

    def test_transaction_failure_rolls_back_sql_and_image_files(self):
        with self.store.connection() as conn:
            conn.execute("CREATE TRIGGER dbo.fail_passage ON dbo.passages AFTER INSERT AS BEGIN THROW 51002, 'isolated-test-failure', 1; END")
        with self.assertRaises(driver().Error): self.store.record(self.event('rollback'))
        self.assertFalse(self.store.occupancy()['occupied'])
        self.assertEqual(self.store.list_visits()['total'],0)
        self.assertEqual(len(list(self.store.images.iterdir())),0)

    def test_list_pagination_keeps_inside_first(self):
        self.store.record(self.event('in'))
        self.store.record(self.event('w1','B','51F00651',1))
        self.store.record(self.event('w2','E','51B50245',2))
        self.assertEqual(self.store.list_visits(limit=1)['items'][0]['status'],'INSIDE')
        self.assertEqual(len(self.store.list_visits(offset=1,limit=1)['items']),1)

    def test_reader_snapshot_prevents_interleaved_gate_change(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.store.connection() as conn:
                conn.execute('BEGIN')
                before = self.store._occupancy(conn)
                future = pool.submit(self.store.record,self.event('in'))
                time.sleep(.15)
                self.assertFalse(future.done())
                self.assertEqual(self.store._occupancy(conn),before)
            self.assertEqual(future.result(timeout=5)['state'],'entry')

    def test_migrate_verifies_all_rows_images_and_retry_without_duplicates(self):
        source = VisitStore(self.temp.name)
        visit_id = source.record(self.event('source'))['visitIds'][0]
        source.confirm_exit(visit_id,'B',self.event('time',seconds=60)['occurred_at'],'Đối chiếu dữ liệu nguồn')
        source.set_deleted(visit_id,True)
        report = migrate(self.settings,self.temp.name,Path(self.temp.name)/'backups')
        self.assertEqual(report['verifiedImages'],2)
        self.assertEqual(self.store.detail(visit_id),source.detail(visit_id))
        self.assertEqual(self.store.occupancy(),source.occupancy())
        again = migrate(self.settings,self.temp.name,Path(self.temp.name)/'backups')
        self.assertEqual(again['counts'],report['counts'])
        self.assertEqual(self.store.list_visits(deleted=True)['total'],1)
        with self.assertRaises(RuntimeError):
            source.set_deleted(visit_id,False)
            migrate(self.settings,self.temp.name,Path(self.temp.name)/'backups')
        self.assertIsNotNone(self.store.detail(visit_id)['deleted_at'])

    def test_cannot_trust_certificate_on_remote_server(self):
        with self.assertRaises(ValueError): connection_string({**self.settings,'server':'remote-sql.example'})

    def test_connection_failure_never_falls_back_to_sqlite(self):
        with self.assertRaises(driver().Error):
            create_visit_store({**self.settings,'driver':'Isolated missing test driver'},self.temp.name)
        self.assertFalse((Path(self.temp.name)/'visits.sqlite3').exists())

    def test_migration_failure_rolls_back_all_target_rows(self):
        source = VisitStore(self.temp.name)
        source.record(self.event('source-in'))
        with self.store.connection() as conn:
            conn.execute("CREATE TRIGGER dbo.fail_passage ON dbo.passages AFTER INSERT AS BEGIN THROW 51002, 'isolated-test-failure', 1; END")
        with self.assertRaises(driver().Error):
            migrate(self.settings,self.temp.name,Path(self.temp.name)/'backups')
        self.assertEqual(self.store.list_visits()['total'],0)
        self.assertEqual(source.list_visits()['total'],1)
        self.assertFalse(self.store.occupancy()['occupied'])


if __name__ == '__main__': unittest.main()
