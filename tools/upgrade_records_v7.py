"""Nâng cấp hồ sơ 6 -> 7 có backup SQL/ảnh và đối chiếu dữ liệu cũ.

Chạy --apply khi dừng worker. Chỉ thêm bảng/cột; không tạo hồ sơ mẫu,
không thay dữ liệu, trạng thái D, ID hoặc ảnh nhận diện đã có.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import uuid

from localize_sqlserver import snapshot,_backup,_ordered
from record_storage import read_settings
from sqlserver_store import open_connection,SqlConnection
from sqlserver_vietnamese import TABLES_V6
from vehicle_records import ROOT,DEFAULT_DATA_DIR


def upgrade(settings,data_dir=DEFAULT_DATA_DIR,backup_root=None):
    raw=open_connection(settings); conn=SqlConnection(raw)
    try:
        version=raw.execute('SELECT MAX(PhienBan) FROM dbo.PhienBanCauTruc').fetchone()[0]
        if version==7: return {'ketQua':'da-nang-cap','phienBan':7}
        if version!=6: raise RuntimeError('Cần cấu trúc 6 trước khi nâng cấp hồ sơ 7.')
        before=snapshot(raw,True)
        ids={t:raw.execute('SELECT OBJECT_ID(?)','dbo.'+physical).fetchone()[0] for t,physical in TABLES_V6.items()}
        backup=Path(backup_root or ROOT/'Data'/'backups')/('ho-so-v7-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        report=dict(database=settings['database'],thuMucSaoLuu=str(backup),
                    **_backup(raw,settings,backup,before,data_dir,purpose='TruocNangCapHoSo7'))
        manifest=backup/'kiem-tra.json'
        report['ketQua']='da-sao-luu-chua-nang-cap'
        manifest.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        conn.begin(True)
        current=snapshot(raw,True)
        if any(_ordered(before[t])!=_ordered(current[t]) for t in before): raise RuntimeError('Dữ liệu đổi trong lúc sao lưu; dừng worker rồi thử lại.')
        raw.execute((ROOT/'tools/sqlserver_management_v7.sql').read_text(encoding='utf-8'))
        after=snapshot(raw,True)
        for table,rows in before.items():
            actual=after[table]
            if table=='schema_migrations': actual=[row for row in actual if row['version']!=7]
            if table=='visits':
                old_columns=set(rows[0]) if rows else set()
                actual=[{key:value for key,value in row.items() if key in old_columns} for row in actual]
            if _ordered(rows)!=_ordered(actual): raise RuntimeError('Không khớp dữ liệu cũ: '+table)
            if ids[table]!=raw.execute('SELECT OBJECT_ID(?)','dbo.'+TABLES_V6[table]).fetchone()[0]: raise RuntimeError('Object ID thay đổi.')
        for table in ('NhatKySuaHoSo','AnhBoSungHoSo'):
            if raw.execute(f'SELECT COUNT(*) FROM dbo.{table}').fetchone()[0]!=0: raise RuntimeError('Bảng mới có dữ liệu không mong đợi.')
        conn.finish(False)
        report.update(ketQua='da-nang-cap',phienBan=7,daDoiChieuDuLieuCu=True,
                      soDongCu={TABLES_V6[t]:len(rows) for t,rows in before.items()})
        manifest.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        return report
    except BaseException:
        conn.finish(True); raise
    finally: raw.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--apply',action='store_true'); args=parser.parse_args()
    settings=read_settings()
    if settings.get('backend')!='sqlserver': raise RuntimeError('Ứng dụng chưa dùng SQL Server.')
    if not args.apply:
        print(json.dumps({'tuPhienBan':6,'denPhienBan':7,'themBang':['NhatKySuaHoSo','AnhBoSungHoSo'],
                          'themCot':['GhiChuBoSung','LyDoCanKiemTra','NguonTao','PhienBanChinhSua']})); return
    for port in (5083,5084,5085):
        with socket.socket() as check:
            if check.connect_ex(('127.0.0.1',port))==0: raise RuntimeError('Dừng worker camera/OCR/hồ sơ trước khi nâng cấp.')
    print(json.dumps(upgrade(settings),ensure_ascii=True,indent=2))


if __name__=='__main__': main()
