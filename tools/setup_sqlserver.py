"""Tạo schema và chuyển SQLite -> SQL Server có backup/đối chiếu.

Mặc định chỉ chuẩn bị database. --activate chỉ được phép khi ba worker đã
dừng; chuyển toàn bộ dữ liệu trong một transaction rồi mới đổi cấu hình.
Không xóa SQLite/ảnh cũ, không ghi đè hồ sơ đã tồn tại khác nội dung ở đích.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import uuid

from record_storage import CONFIG_PATH
from sqlserver_store import open_connection, SqlConnection, SqlServerVisitStore
from vehicle_records import ROOT, DEFAULT_DATA_DIR, utc_time

TABLES = ('visits','passages','gate_state','plate_release','visit_corrections','record_changes','visit_attachments')
TIME_COLUMNS = {'entered_at','exited_at','created_at','deleted_at','occurred_at','confirmed_at','last_seen_at'}


def ensure_database(settings):
    database = settings['database']
    if not re.fullmatch(r'[A-Za-z0-9_]{1,100}', database): raise ValueError('Tên DB không hợp lệ.')
    raw = open_connection(settings, 'master')
    try:
        if raw.execute('SELECT DB_ID(?)', database).fetchone()[0] is None:
            raw.execute(f'CREATE DATABASE [{database}]')
    finally: raw.close()
    raw = open_connection(settings)
    conn = SqlConnection(raw)
    try:
        if raw.execute("SELECT OBJECT_ID(N'dbo.visits',N'U')").fetchone()[0] is not None:
            raise RuntimeError('Database còn tên tiếng Anh; chạy tools/localize_sqlserver.py trước, không tạo bảng trùng.')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute((ROOT / 'tools' / 'sqlserver_schema.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'tools' / 'sqlserver_management_v7.sql').read_text(encoding='utf-8'))
        conn.finish(False)
    except BaseException:
        conn.finish(True)
        raise
    finally: raw.close()


def normalize(row):
    return {key:utc_time(value) if key in TIME_COLUMNS and value is not None else value for key,value in dict(row).items()}


def snapshot_sqlite(path):
    raw = sqlite3.connect(path); raw.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {table:[normalize(row) for row in raw.execute(f'SELECT * FROM {table}')] if table in tables else [] for table in TABLES}
        for row in result['visits']:
            for column,value in [('notes',''),('review_note',''),('origin','camera'),('edit_version',0)]: row.setdefault(column,value)
        return result
    finally: raw.close()


def same_rows(source, target):
    def order(rows): return sorted(rows,key=lambda row:json.dumps(row,sort_keys=True,ensure_ascii=False))
    return order(source) == order(target)


def migrate(settings, data_dir=DEFAULT_DATA_DIR, backup_root=None):
    data_dir = Path(data_dir).resolve()
    backup_root = Path(backup_root or ROOT / 'Data' / 'backups').resolve()
    backup = backup_root / ('sqlserver-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    source = data_dir / 'visits.sqlite3'
    # SQLite backup API gồm cả các transaction đã commit còn nằm trong WAL.
    original = sqlite3.connect(f'{source.as_uri()}?mode=ro',uri=True)
    copy = sqlite3.connect(backup / 'visits.sqlite3')
    try:
        original.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite backup không toàn vẹn; không chuyển dữ liệu.')
    finally: copy.close(); original.close()
    tables = snapshot_sqlite(backup / 'visits.sqlite3')
    (backup / 'evidence').mkdir()
    copied = set()
    for passage in tables['passages']:
        for key, checksum in [('scene_file','scene_sha256'),('crop_file','crop_sha256')]:
            name = passage[key]
            if not re.fullmatch(r'[0-9a-f]{32}_(scene|plate)\.jpg',name):
                raise RuntimeError('Tên ảnh cũ không hợp lệ; cần đối chiếu trước khi chuyển.')
            image = data_dir / 'evidence' / name
            if hashlib.sha256(image.read_bytes()).hexdigest() != passage[checksum]:
                raise RuntimeError('Ảnh cũ thiếu hoặc sai checksum; không kích hoạt SQL Server.')
            if name not in copied:
                saved = backup / 'evidence' / name
                shutil.copy2(image,saved)
                if hashlib.sha256(saved.read_bytes()).hexdigest() != passage[checksum]:
                    raise RuntimeError('Ảnh backup chưa khớp; không kích hoạt SQL Server.')
                copied.add(name)
    for attachment in tables['visit_attachments']:
        name = attachment['file_name']
        if not re.fullmatch(r'[0-9a-f]{32}_upload\.(jpg|png|webp)',name): raise RuntimeError('Tên ảnh bổ sung không hợp lệ.')
        source_image = data_dir/'evidence'/name
        if hashlib.sha256(source_image.read_bytes()).hexdigest()!=attachment['sha256']: raise RuntimeError('Ảnh bổ sung nguồn thiếu/sai mã kiểm tra.')
        shutil.copy2(source_image,backup/'evidence'/name)
        if hashlib.sha256((backup/'evidence'/name).read_bytes()).hexdigest()!=attachment['sha256']: raise RuntimeError('Ảnh bổ sung backup không khớp.')
        copied.add(name)
    store = SqlServerVisitStore(settings,data_dir)
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = {table:[dict(row) for row in conn.execute(f'SELECT * FROM {table}')] for table in TABLES}
        populated = any(existing[table] for table in TABLES if table != 'gate_state')
        if populated:
            if not all(same_rows(tables[table],existing[table]) for table in TABLES):
                raise RuntimeError('DB đích đã có dữ liệu khác; không ghi đè hoặc trộn tự động.')
        else:
            if existing['gate_state'] != [{'id':1,'revision':0}]:
                raise RuntimeError('Trạng thái DB đích đã thay đổi; không ghi đè.')
            for table in TABLES:
                for row in tables[table]:
                    if table == 'gate_state':
                        conn.execute('UPDATE gate_state SET revision=? WHERE id=1',(row['revision'],)); continue
                    columns = list(row)
                    # Tên bảng/cột được lấy từ schema source của chính project;
                    # chỉ chấp nhận identifier đơn, giá trị luôn dùng parameter.
                    if any(not re.fullmatch('[a-z_][a-z0-9_]*',key) for key in columns): raise ValueError('Cột source không hợp lệ.')
                    conn.execute(f"INSERT INTO {table} ({','.join('['+key+']' for key in columns)}) VALUES ({','.join('?' for _ in columns)})",tuple(row.values()))
        # Đối chiếu cả nội dung từng cột, không chỉ đếm số dòng.
        for table in TABLES:
            actual = [dict(row) for row in conn.execute(f'SELECT * FROM {table}')]
            if not same_rows(tables[table],actual): raise RuntimeError(f'Dữ liệu bảng {table} chưa khớp.')
        counts = {table:len(rows) for table,rows in tables.items()}
        conn.execute('INSERT INTO migration_runs(id,source_sha256,backup_path,counts_json) VALUES (?,?,?,?)',
                     (uuid.uuid4().hex,hashlib.sha256((backup/'visits.sqlite3').read_bytes()).hexdigest(),str(backup),json.dumps(counts)))
    report = {'server':settings['server'],'database':settings['database'],'counts':counts,'verifiedImages':len(copied),'backup':str(backup)}
    (backup / 'verification.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server',default=r'.\SQLEXPRESS')
    parser.add_argument('--database',default='QuanLyXe')
    parser.add_argument('--trust-local-certificate',action='store_true')
    parser.add_argument('--activate',action='store_true')
    args = parser.parse_args()
    settings = {'backend':'sqlserver','server':args.server,'database':args.database,
                'driver':'ODBC Driver 18 for SQL Server','trustServerCertificate':args.trust_local_certificate}
    if args.activate:
        for port in (5083,5084,5085):
            with socket.socket() as listener:
                listener.settimeout(.3)
                if listener.connect_ex(('127.0.0.1',port)) == 0:
                    raise RuntimeError('Phải dừng các worker 5083/5084/5085 trước khi chuyển nguồn lưu trữ.')
    ensure_database(settings)
    if args.activate:
        report = migrate(settings)
        backup = Path(report['backup'])
        if CONFIG_PATH.exists(): shutil.copy2(CONFIG_PATH,backup/'database.previous.json')
        staged = CONFIG_PATH.with_suffix('.json.new')
        staged.write_text(json.dumps(settings,indent=2,ensure_ascii=False),encoding='utf-8')
        staged.replace(CONFIG_PATH)
        print(json.dumps(report,ensure_ascii=False))
    else: print(json.dumps({'schemaReady':True,'server':args.server,'database':args.database}))


if __name__ == '__main__': main()
