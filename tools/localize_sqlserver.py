"""Nâng cấp tại chỗ tên SQL Server 5 -> 6, không tạo lại lượt xe.

Chạy --apply sau khi dừng các worker camera/OCR/hồ sơ. Trước khi đổi tên:
backup SQL Server COPY_ONLY + CHECKSUM + VERIFYONLY, sao lưu ảnh và đối chiếu.
DDL, kiểm tra nội dung và phiên bản nằm trong một transaction. Không dịch giá trị.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import uuid

from record_storage import read_settings
from sqlserver_store import open_connection, SqlConnection, SqlCursor
from sqlserver_vietnamese import TABLES_V6 as TABLES, COLUMNS
from vehicle_records import ROOT, DEFAULT_DATA_DIR


def snapshot(raw, localized=False):
    """Trả tên nội bộ để đối chiếu trước/sau mà không phụ thuộc tên vật lý."""
    return {table: [dict(row) for row in SqlCursor(raw.execute(
        f'SELECT * FROM dbo.[{physical if localized else table}]'))]
        for table, physical in TABLES.items()}


def _ordered(rows):
    return sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)


def _object_ids(raw, localized):
    return {table: raw.execute('SELECT OBJECT_ID(?,N\'U\')',
            'dbo.' + (physical if localized else table)).fetchone()[0]
            for table, physical in TABLES.items()}


def _rename(raw, old, new, kind):
    # Tên object từ danh mục/schema cố định, vẫn truyền qua tham số sp_rename.
    raw.execute('EXEC sys.sp_rename @objname=?, @newname=?, @objtype=?', old, new, kind)


def rename_schema(raw, before=None):
    """Caller giữ transaction/khóa Exclusive; lỗi phải rollback toàn bộ DDL.

    Hàm lõi không tạo backup hoặc mở port: dùng trong kiểm thử DB UUID riêng.
    Công cụ vận hành localize() luôn backup trước khi gọi hàm này.
    """
    before = before or snapshot(raw)
    if max(row['version'] for row in before['schema_migrations']) != 5:
        raise RuntimeError('Chỉ nâng cấp cấu trúc 5 sang 6; không đoán phiên bản.')
    before_ids = _object_ids(raw, False)
    if not all(before_ids.values()) or any(_object_ids(raw, True).values()):
        raise RuntimeError('Tên bảng không đúng cấu trúc 5 hoặc đã tồn tại tên đích.')
    dependencies = raw.execute('''SELECT DISTINCT o.name FROM sys.sql_expression_dependencies d
        JOIN sys.objects o ON o.object_id=d.referencing_id
        WHERE d.referenced_id IN (''' + ','.join('?' for _ in before_ids) + ''')
        AND o.type IN ('V','P','FN','IF','TF','TR')''', tuple(before_ids.values())).fetchall()
    if dependencies:
        raise RuntimeError('Có view/procedure/trigger phụ thuộc cần cập nhật trước: ' + ', '.join(r[0] for r in dependencies))
    for table, physical in TABLES.items():
        columns = [row[0] for row in raw.execute('SELECT name FROM sys.columns WHERE object_id=?', before_ids[table])]
        if any(column not in COLUMNS for column in columns):
            raise RuntimeError('Có cột ngoài cấu trúc đã biết trong ' + table)
        if table == 'gate_state':
            # SQL Server 2025 chặn đổi tên cột tham gia CHECK. Tạm bỏ đúng
            # CHECK(id=1) đã biết, rồi tạo lại tương đương trong cùng transaction.
            checks = raw.execute('SELECT name,definition,is_disabled,is_not_trusted FROM sys.check_constraints WHERE parent_object_id=?', before_ids[table]).fetchall()
            if len(checks) != 1 or re.sub(r'[\s\[\]()]','',checks[0][1]).lower() != 'id=1' or checks[0][2] or checks[0][3]:
                raise RuntimeError('CHECK của gate_state khác cấu trúc chuẩn; không tự thay đổi.')
            if not re.fullmatch('[A-Za-z_0-9]+', checks[0][0]): raise RuntimeError('Tên CHECK không hợp lệ.')
            raw.execute(f'ALTER TABLE dbo.gate_state DROP CONSTRAINT [{checks[0][0]}]')
        _rename(raw, 'dbo.' + table, physical, 'OBJECT')
        for column in columns:
            _rename(raw, f'dbo.{physical}.{column}', COLUMNS[column], 'COLUMN')
    raw.execute('ALTER TABLE dbo.TrangThaiKhuD WITH CHECK ADD CONSTRAINT DieuKien_TrangThaiKhuD_Ma CHECK(Ma=1)')

    # Đổi cả tên khóa/ràng buộc và index của ứng dụng để dễ đọc trong SSMS.
    # FK vẫn trỏ object_id cũ; không drop bảng hoặc tạo lại ID.
    for table, physical in TABLES.items():
        constraints = raw.execute('''SELECT o.name,o.type,c.name FROM sys.objects o
            LEFT JOIN sys.default_constraints d ON d.object_id=o.object_id
            LEFT JOIN sys.columns c ON c.object_id=o.parent_object_id AND c.column_id=d.parent_column_id
            WHERE o.parent_object_id=? AND o.type IN ('PK','F','UQ','D','C')''', before_ids[table]).fetchall()
        for old, kind, column in constraints:
            kind = kind.strip()
            if kind == 'PK': name = 'KhoaChinh_' + physical
            elif kind == 'F': name = 'KhoaNgoai_' + physical + '_MaLuotXe'
            elif kind == 'UQ': name = 'DuyNhat_' + physical + '_MaSuKien'
            elif kind == 'C': name = 'DieuKien_' + physical + '_Ma'
            else: name = 'MacDinh_' + physical + '_' + column
            if old != name:
                _rename(raw, 'dbo.' + old, name, 'OBJECT')
    indexes = {'visits': {'ix_visits_open': 'ChiMuc_LuotXeRaVao_BienSo',
                          'ix_visits_date': 'ChiMuc_LuotXeRaVao_ThoiGianTao'},
               'passages': {'ix_passages_visit': 'ChiMuc_LanGhiNhanCamera_MaLuotXe'},
               'visit_corrections': {'ix_corrections_visit': 'ChiMuc_DieuChinhLuotXe_MaLuotXe'}}
    for table, names in indexes.items():
        for old, new in names.items():
            _rename(raw, f'dbo.{TABLES[table]}.{old}', new, 'INDEX')
    raw.execute('INSERT INTO dbo.PhienBanCauTruc(PhienBan) VALUES(6)')
    after = snapshot(raw, True)
    for table in TABLES:
        compared = after[table]
        if table == 'schema_migrations':
            compared = [row for row in compared if row['version'] != 6]
        if _ordered(before[table]) != _ordered(compared):
            raise RuntimeError('Nội dung sau đổi tên chưa khớp: ' + table)
    if _object_ids(raw, True) != before_ids:
        raise RuntimeError('Object ID của bảng bị thay đổi; không hoàn tất.')
    check = raw.execute('DBCC CHECKCONSTRAINTS WITH ALL_CONSTRAINTS')
    while True:
        if check.description and check.fetchall():
            raise RuntimeError('Liên kết/ràng buộc không hợp lệ sau đổi tên.')
        if not check.nextset(): break
    return after


def _backup(raw, settings, backup, before, data_dir, purpose='TruocVietHoa'):
    database = settings['database']
    if not re.fullmatch('[A-Za-z0-9_]{1,100}', database): raise ValueError('Tên DB không hợp lệ.')
    # Dùng thư mục backup mặc định của SQL Server mà tài khoản dịch vụ có quyền ghi.
    # Không cấp thêm quyền Windows hoặc ghi đè một file backup đã có.
    directory = raw.execute("SELECT CONVERT(nvarchar(4000),SERVERPROPERTY('InstanceDefaultBackupPath'))").fetchone()[0]
    if not directory: raise RuntimeError('Không xác định được thư mục backup mặc định của SQL Server.')
    if not re.fullmatch('[A-Za-z0-9_]+',purpose): raise ValueError('Tên backup không hợp lệ.')
    native = str(Path(directory) / (database + '_' + purpose + '_' + backup.name + '.bak'))
    raw.timeout = 120
    cursor = raw.execute(f'BACKUP DATABASE [{database}] TO DISK=? WITH COPY_ONLY,CHECKSUM', native)
    while cursor.nextset(): pass
    cursor = raw.execute('RESTORE VERIFYONLY FROM DISK=? WITH CHECKSUM', native)
    while cursor.nextset(): pass
    (backup / 'du-lieu-truoc-doi-ten.json').write_text(json.dumps(before, indent=2, ensure_ascii=False), encoding='utf-8')
    images = backup / 'evidence'; images.mkdir()
    hashes = {}
    for passage in before['passages']:
        for field, checksum in [('scene_file','scene_sha256'),('crop_file','crop_sha256')]:
            name = passage[field]
            if not re.fullmatch(r'[0-9a-f]{32}_(scene|plate)\.jpg', name): raise RuntimeError('Tên ảnh không hợp lệ.')
            source = Path(data_dir) / 'evidence' / name
            if hashlib.sha256(source.read_bytes()).hexdigest() != passage[checksum]:
                raise RuntimeError('Ảnh nguồn thiếu/sai mã kiểm tra: ' + name)
            if name not in hashes:
                shutil.copy2(source, images / name)
                if hashlib.sha256((images/name).read_bytes()).hexdigest() != passage[checksum]:
                    raise RuntimeError('Ảnh backup không khớp.')
                hashes[name] = passage[checksum]
    return {'tepSaoLuuSQLServer': native, 'daXacMinhSaoLuuSQLServer': True,
            'soAnhDaKiemTra': len(hashes), 'maKiemTraAnh': hashes}


def localize(settings, data_dir=DEFAULT_DATA_DIR, backup_root=None):
    """Đường chạy vận hành: backup được xác minh rồi mới đổi tên trong transaction."""
    raw = open_connection(settings)
    conn = SqlConnection(raw)
    try:
        if raw.execute("SELECT OBJECT_ID(N'dbo.LuotXeRaVao',N'U')").fetchone()[0] is not None:
            if raw.execute('SELECT MAX(PhienBan) FROM dbo.PhienBanCauTruc').fetchone()[0] not in (6,7):
                raise RuntimeError('Tên đích đã tồn tại nhưng cấu trúc chưa đúng 6.')
            return {'ketQua': 'da-viet-hoa', 'database': settings['database']}
        before = snapshot(raw)
        backup = Path(backup_root or ROOT/'Data'/'backups') / ('viet-hoa-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        report = {'database': settings['database'], 'thuMucSaoLuu': str(backup), **_backup(raw, settings, backup, before, data_dir)}
        manifest = backup/'kiem-tra.json'
        report['ketQua'] = 'da-sao-luu-chua-doi-ten'
        manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        conn.begin(True)
        current = snapshot(raw)
        if any(_ordered(before[t]) != _ordered(current[t]) for t in TABLES):
            raise RuntimeError('Dữ liệu thay đổi trong khi backup; cần dừng worker trước.')
        after = rename_schema(raw, before)
        conn.finish(False)
        report.update(ketQua='da-viet-hoa', daDoiChieuTungCot=True, giuNguyenMaBang=True,
                      soDongTruoc={TABLES[t]: len(rows) for t, rows in before.items()},
                      soDong={TABLES[t]: len(rows) for t, rows in after.items()})
        manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        return report
    except BaseException:
        conn.finish(True)
        raise
    finally: raw.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    settings = read_settings()
    if settings.get('backend') != 'sqlserver': raise RuntimeError('Ứng dụng chưa dùng SQL Server.')
    if not args.apply:
        print(json.dumps({'bang': TABLES, 'cot': COLUMNS}, ensure_ascii=True, indent=2))
        return
    for port in (5083,5084,5085):
        with socket.socket() as check:
            if check.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError('Dừng worker camera/OCR/hồ sơ trước khi đổi tên DB.')
    print(json.dumps(localize(settings), ensure_ascii=True, indent=2))


if __name__ == '__main__': main()
