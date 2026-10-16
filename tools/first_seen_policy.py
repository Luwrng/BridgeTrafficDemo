"""Đổi hồ sơ camera cũ sang giờ vào = lần nhận diện đầu, giữ ảnh và audit.

Không chạy tự động lúc khởi động/đọc API. CLI chỉ áp dụng sau backup SQL
được VERIFYONLY và kiểm tra dữ liệu không đổi trong lúc sao lưu.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from vehicle_records import ROOT, DEFAULT_DATA_DIR

OLD_WAITING_NOTE = 'Đã thấy xe ra ở camera khác sau khi chờ; thiếu lần xác nhận vào khi D trống. Cần đối chiếu thời gian vào.'
OLD_REPAIRED_NOTE = 'Đối chiếu lỗi cũ: lần nhận diện sau khi chờ ở camera khác là cổng ra; thiếu lần xác nhận thời gian vào D.'
AUTOMATIC_NOTES = ('', OLD_WAITING_NOTE, OLD_REPAIRED_NOTE)


def snapshot(conn):
    from sqlserver_vietnamese import TABLES
    return {table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in TABLES}


def ordered(value):
    return {table:sorted(json.dumps(row,sort_keys=True,ensure_ascii=False) for row in rows) for table,rows in value.items()}


def apply_first_seen_policy(store, expected=None):
    """Chỉ bổ sung giờ đang thiếu từ bằng chứng gốc; không đụng lượt sửa tay.

    WAITING vẫn WAITING, không đưa xe chờ vào D. Lượt đã có cổng/giờ ra
    chuyển COMPLETE. Giờ ra, ID và ảnh/hash giữ nguyên. Không tự đóng xe.
    """
    changes=[]
    with store.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if expected is not None and ordered(snapshot(conn))!=ordered(expected):
            raise RuntimeError('Dữ liệu đã thay đổi trong lúc backup; chưa áp dụng quy tắc.')
        before_occupancy=store._occupancy(conn)
        rows=conn.execute("""SELECT * FROM visits WHERE realm='real' AND origin='camera'
            AND entered_at IS NULL AND status IN ('WAITING','NEEDS_REVIEW')""").fetchall()
        for row in rows:
            before=dict(row)
            if row['review_note'] not in AUTOMATIC_NOTES: continue
            if row['status']=='NEEDS_REVIEW' and row['issue'] not in (OLD_WAITING_NOTE,OLD_REPAIRED_NOTE): continue
            actions=[r[0] for r in conn.execute('SELECT action FROM record_changes WHERE visit_id=?',(row['id'],))]
            if any(action.startswith('manual-') for action in actions): continue
            first=conn.execute("""SELECT * FROM passages WHERE visit_id=? AND direction IN ('entry','waiting')
                ORDER BY occurred_at,id LIMIT 1""",(row['id'],)).fetchone()
            if not first or (row['exited_at'] and (not row['exit_camera'] or first['occurred_at']>=row['exited_at'])): continue
            status='COMPLETE' if row['exited_at'] else 'WAITING'
            # Cùng ảnh thật; đổi phân loại lần thấy đầu theo quy tắc vừa chốt.
            conn.execute("UPDATE passages SET direction='entry' WHERE id=?",(first['id'],))
            conn.execute("""UPDATE visits SET entered_at=?,entry_camera=?,status=?,issue=?,review_note='',
                edit_version=edit_version+1 WHERE id=?""",
                (first['occurred_at'],first['camera_id'],status,'Chờ ngoài D' if status=='WAITING' else None,row['id']))
            after=dict(conn.execute('SELECT * FROM visits WHERE id=?',(row['id'],)).fetchone())
            before['passage_directions']={first['id']:first['direction']}
            after['passage_directions']={first['id']:'entry'}
            conn.execute('INSERT INTO record_changes(id,visit_id,action,created_at,reason,before_json,after_json) VALUES (?,?,?,?,?,?,?)',
                (uuid.uuid4().hex,row['id'],'first-seen-policy',datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                 'Theo quy tắc đã chốt: cổng/giờ vào là lần camera nhận diện biển số đầu tiên.',
                 json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False)))
            changes.append({'id':row['id'],'before':before,'after':after})
        if changes: conn.execute('UPDATE gate_state SET revision=revision+1 WHERE id=1')
        after_occupancy=store._occupancy(conn)
        # Đổi quy tắc giờ hồ sơ không được tự giải phóng/cấp quyền chiếm D.
        for field in ('occupied','vehicle','waitingCount','issue'):
            if before_occupancy[field]!=after_occupancy[field]: raise RuntimeError('Trạng thái D thay đổi ngoài dự kiến.')
    return {'changed':len(changes),'changes':changes,'occupancy':after_occupancy}


def main():
    from record_storage import read_settings, create_visit_store
    from sqlserver_store import open_connection
    from localize_sqlserver import _backup
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    settings=read_settings()
    if settings['backend']!='sqlserver': raise RuntimeError('CLI vận hành chỉ dành cho SQL Server.')
    store=create_visit_store(settings)
    if not args.apply:
        with store.connection() as conn:
            count=conn.execute("SELECT COUNT(*) FROM visits WHERE origin='camera' AND entered_at IS NULL AND status IN ('WAITING','NEEDS_REVIEW')").fetchone()[0]
        print(json.dumps({'candidateCount':count,'applied':False})); return
    with store.connection() as conn:
        conn.execute('BEGIN'); before=snapshot(conn)
    backup=ROOT/'Data'/'backups'/('gio-vao-lan-dau-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    raw=open_connection(settings)
    try: report=_backup(raw,settings,backup,before,DEFAULT_DATA_DIR,purpose='TruocDoiQuyTacGioVao')
    finally: raw.close()
    # Sao lưu cả ảnh bổ sung, ngoài hai ảnh OCR mà _backup đã kiểm tra.
    for item in before['visit_attachments']:
        name=item['file_name']
        if Path(name).name!=name: raise RuntimeError('Tên ảnh bổ sung không hợp lệ.')
        source=store.images/name
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']: raise RuntimeError('Ảnh bổ sung không khớp.')
        shutil.copy2(source,backup/'evidence'/name)
    manifest=backup/'kiem-tra.json'
    report.update(state='backed-up-not-applied')
    manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    result=apply_first_seen_policy(store,expected=before)
    report.update(state='applied',**result)
    manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'changed':result['changed'],'backup':str(backup),'nativeBackupVerified':True,
                      'occupied':result['occupancy']['occupied']},ensure_ascii=True))


if __name__=='__main__': main()
