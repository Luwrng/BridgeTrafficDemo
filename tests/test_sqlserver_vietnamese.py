"""Kiểm tra ánh xạ tên và nâng cấp SQL tại chỗ, không dùng hồ sơ thật."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from sqlserver_vietnamese import TABLES, COLUMNS, translate_sql
from sqlserver_store import SqlServerVisitStore, SqlConnection, open_connection
from setup_sqlserver import snapshot_sqlite, ensure_database
from localize_sqlserver import snapshot, rename_schema, localize, _rename
from upgrade_records_v7 import upgrade
from vehicle_records import VisitStore


class VietnameseMappingTests(unittest.TestCase):
    def test_all_parameter_values_and_status_codes_stay_unchanged(self):
        sql, args = translate_sql("UPDATE visits SET status=?,issue=? WHERE plate=? AND realm='real'",
                                   ('INSIDE', 'INSIDE', 'COMPLETE'))
        self.assertIn("[LoaiDuLieu]='real'", sql)
        self.assertEqual(args, ('INSIDE', 'INSIDE', 'COMPLETE'))

    def test_insert_and_implicit_audit_columns(self):
        sql, args = translate_sql('INSERT INTO visit_corrections VALUES (?,?,?,?,?,?,?)',
                                  ('id', 'visit', 'confirm-exit', 'B', 'time', 'time', 'confirm-exit'))
        self.assertIn('[DieuChinhLuotXe]', sql)
        self.assertEqual(args[2], 'confirm-exit')
        self.assertEqual(args[-1], 'confirm-exit')

    def test_literals_comments_bracketed_names_and_in(self):
        sql, _ = translate_sql("SELECT [status], 'visits' AS label FROM [visits] WHERE [status] IN ('INSIDE','WAITING') -- status='COMPLETE'")
        self.assertIn("SELECT [TrangThai], 'visits' AS label FROM [LuotXeRaVao]", sql)
        self.assertIn("IN ('INSIDE','WAITING')", sql)
        self.assertTrue(sql.endswith("-- status='COMPLETE'"))

    def test_timestamp_and_source_metadata_stay_unmodified(self):
        _, args = translate_sql('INSERT INTO passages(direction,source_kind,source_name,occurred_at) VALUES(?,?,?,?)',
                                ('exit', 'live', 'live', '2026-10-15T01:00:00+00:00'))
        self.assertEqual(args, ('exit', 'live', 'live', '2026-10-15T01:00:00+00:00'))


@unittest.skipUnless(os.environ.get('CAMERA_SQLSERVER_TESTS') == '1', 'SQL Server integration opt-in')
class VietnameseUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.database = 'QuanLyXe_Test_' + uuid.uuid4().hex
        self.settings = {'backend':'sqlserver', 'server':r'.\SQLEXPRESS',
                         'database':self.database, 'trustServerCertificate':True}
        master = open_connection(self.settings, 'master')
        try: master.execute(f'CREATE DATABASE [{self.database}]')
        finally: master.close()
        self.addCleanup(self.drop_test_database)
        raw = open_connection(self.settings)
        try: raw.execute((ROOT/'tools/sqlserver_schema_v5.sql').read_text(encoding='utf-8'))
        finally: raw.close()
        # Tạo lượt bằng nghiệp vụ SQLite thật, rồi chép nguyên mẫu vào schema 5.
        # Chỉ nằm trong DB UUID của bài test, không nhập vào QuanLyXe.
        source = VisitStore(self.temp.name)
        start = datetime(2026,10,14,3,tzinfo=timezone.utc)
        scene = (ROOT/'artifacts/ui-check/camera-live.jpg').read_bytes()
        crop = (ROOT/'artifacts/ui-check/plate-crop.jpg').read_bytes()
        def event(key, camera, seconds, plate='51K73109'):
            return dict(event_key=key,camera_id=camera,plate=plate,occurred_at=(start+timedelta(seconds=seconds)).isoformat(),
                        source_kind='live',media_time=seconds,confidence=.9,scene=scene,crop=crop)
        first = source.record(event('entry','A',0))['visitIds'][0]
        source.confirm_exit(first,'B',(start+timedelta(seconds=10)).isoformat(),'INSIDE')
        source.set_deleted(first,True)
        source.record(event('second','C',20,'51F00651'))
        source.record(event('waiting','E',21,'51B50245'))
        raw = open_connection(self.settings)
        try:
            for table, rows in snapshot_sqlite(source.database).items():
                if table in ('record_changes','visit_attachments'): continue
                for row in rows:
                    if table == 'gate_state':
                        raw.execute('UPDATE gate_state SET revision=? WHERE id=1',row['revision']); continue
                    old_columns={r[0] for r in raw.execute('SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)','dbo.'+table)}
                    row={k:v for k,v in row.items() if k in old_columns}
                    raw.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
            raw.execute('INSERT INTO migration_runs(id,source_sha256,backup_path,counts_json) VALUES(?,?,?,?)',
                        uuid.uuid4().hex,'a'*64,self.temp.name,json.dumps({'visits':3,'passages':3}))
        finally: raw.close()
        self.expected_occupancy = source.occupancy()
        self.expected_visit = source.detail(first)
        self.expected_visit['edit_version'] = 0  # Cấu trúc 5 chưa có bộ đếm chỉnh sửa.

    def drop_test_database(self):
        if not re.fullmatch('QuanLyXe_Test_[0-9a-f]{32}',self.database): raise RuntimeError('Sai tên DB kiểm thử.')
        raw = open_connection(self.settings,'master')
        try:
            raw.execute(f'ALTER DATABASE [{self.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE')
            raw.execute(f'DROP DATABASE [{self.database}]')
        finally: raw.close()

    def test_in_place_upgrade_preserves_rows_objects_occupancy_and_evidence(self):
        report = localize(self.settings,self.temp.name,Path(self.temp.name)/'backups')
        native = Path(report['tepSaoLuuSQLServer'])
        self.assertEqual(report['soAnhDaKiemTra'],6)
        self.assertTrue(native.name.startswith(self.database+'_TruocVietHoa_'))
        upgraded=upgrade(self.settings,self.temp.name,Path(self.temp.name)/'upgrade')
        self.assertTrue(upgraded['daDoiChieuDuLieuCu'])
        self.assertEqual(upgrade(self.settings,self.temp.name)['ketQua'],'da-nang-cap')
        store = SqlServerVisitStore(self.settings,self.temp.name)
        self.assertEqual(store.occupancy(),self.expected_occupancy)
        self.assertEqual(store.detail(self.expected_visit['id']),self.expected_visit)
        self.assertEqual(localize(self.settings,self.temp.name)['ketQua'],'da-viet-hoa')
        ensure_database(self.settings)  # Chạy setup lại không tạo bảng trùng hoặc ghi lại trạng thái D.
        raw = open_connection(self.settings)
        try:
            # Windows không cấp quyền đọc thư mục backup của dịch vụ SQL cho
            # user hiện tại. Kiểm tra bằng SQL Server, không thay quyền Windows.
            header = raw.execute('RESTORE HEADERONLY FROM DISK=?',str(native))
            fields = [c[0] for c in header.description]
            self.assertEqual(dict(zip(fields,header.fetchall()[0]))['DatabaseName'],self.database)
            while header.nextset(): pass
            tables = {r[0] for r in raw.execute('SELECT name FROM sys.tables')}
            self.assertEqual(tables,set(TABLES.values()))
            self.assertEqual(raw.execute("SELECT TrangThai FROM dbo.LuotXeRaVao WHERE Ma=?",self.expected_visit['id']).fetchone()[0],'COMPLETE')
            self.assertEqual(tuple(raw.execute('SELECT ThaoTac,LyDo FROM dbo.DieuChinhLuotXe').fetchone()),('confirm-exit','INSIDE'))
            self.assertEqual(raw.execute('SELECT COUNT(*) FROM dbo.PhienBanCauTruc').fetchone()[0],3)
            columns = {r[0] for r in raw.execute('SELECT name FROM sys.columns WHERE object_id IN (SELECT object_id FROM sys.tables)')}
            self.assertFalse(columns & set(COLUMNS))
            names = [r[0] for r in raw.execute("SELECT name FROM sys.objects WHERE type IN ('PK','F','UQ','D','C') AND parent_object_id IN (SELECT object_id FROM sys.tables)")]
            self.assertTrue(all(n.startswith(('KhoaChinh_','KhoaNgoai_','DuyNhat_','MacDinh_','DieuKien_')) for n in names))
        finally: raw.close()

    def test_failed_rename_rolls_back_table_and_column_changes(self):
        raw = open_connection(self.settings); conn = SqlConnection(raw)
        before = snapshot(raw)
        calls = 0
        def fail_after_some_ddl(*args):
            nonlocal calls
            calls += 1
            if calls == 5: raise RuntimeError('isolated-test-failure')
            return _rename(*args)
        try:
            conn.begin(True)
            with patch('localize_sqlserver._rename',side_effect=fail_after_some_ddl):
                with self.assertRaisesRegex(RuntimeError,'isolated-test-failure'): rename_schema(raw)
            conn.finish(True)
            self.assertEqual(snapshot(raw),before)
            self.assertIsNone(raw.execute("SELECT OBJECT_ID('dbo.LuotXeRaVao')").fetchone()[0])
            with self.assertRaisesRegex(RuntimeError,'còn tên tiếng Anh'): ensure_database(self.settings)
        finally: conn.finish(True); raw.close()


if __name__ == '__main__': unittest.main()
