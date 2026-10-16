"""Quản lý hồ sơ bổ sung; dùng cùng transaction/khóa với luồng camera.

Ảnh nhập tay có bảng riêng, không giả làm ảnh OCR. Mọi sửa đổi lưu trước/sau
và lý do; edit_version chặn ghi đè khi camera hoặc người khác vừa đổi hồ sơ.
"""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
import re
import uuid

CAMERAS = ('A', 'B', 'C', 'E')
STATES = ('INSIDE', 'COMPLETE', 'WAITING', 'NEEDS_REVIEW')
EDIT_FIELDS = ('plate','entered_at','exited_at','entry_camera','exit_camera','status','notes','review_note')


class RecordConflict(ValueError):
    """Client cần đọc lại dữ liệu mới, không tự gửi đè phiên bản cũ."""


def _utc(value):
    result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None: raise ValueError('Ngày giờ phải có múi giờ.')
    return result.astimezone(timezone.utc).isoformat(timespec='milliseconds')


def _photo_sql(direction):
    return f"(EXISTS (SELECT 1 FROM passages p WHERE p.visit_id=v.id AND p.direction='{direction}') OR EXISTS (SELECT 1 FROM visit_attachments a WHERE a.visit_id=v.id AND a.direction='{direction}'))"


def _review_sql():
    return f"""(COALESCE(v.review_note,'')<>'' OR v.status='NEEDS_REVIEW'
        OR (v.status<>'WAITING' AND (v.entered_at IS NULL OR v.entry_camera IS NULL))
        OR (v.exited_at IS NOT NULL AND v.exit_camera IS NULL)
        OR (v.exit_camera IS NOT NULL AND v.exited_at IS NULL AND v.status<>'WAITING')
        OR (v.status='COMPLETE' AND v.exited_at IS NULL)
        OR (v.entered_at IS NOT NULL AND NOT {_photo_sql('entry')})
        OR (v.exited_at IS NOT NULL AND NOT {_photo_sql('exit')}))"""


def review_reasons(visit):
    reasons = []
    if visit.get('review_note'): reasons.append(visit['review_note'])
    if visit['status'] != 'WAITING':
        if not visit.get('entered_at'): reasons.append('Thiếu thời gian vào')
        if not visit.get('entry_camera'): reasons.append('Thiếu cổng vào')
        if visit.get('exited_at') and not visit.get('exit_camera'): reasons.append('Thiếu cổng ra')
        if (visit.get('exit_camera') or visit['status']=='COMPLETE') and not visit.get('exited_at'): reasons.append('Thiếu thời gian ra')
        if visit.get('entered_at') and not visit.get('has_entry_photo'): reasons.append('Thiếu ảnh vào')
        if visit.get('exited_at') and not visit.get('has_exit_photo'): reasons.append('Thiếu ảnh ra')
    if visit['status']=='NEEDS_REVIEW' and not reasons: reasons.append('Lượt xe chưa đủ thông tin')
    return reasons


class RecordManagementMixin:
    def _management_columns(self, conn):
        # Chỉ gọi từ constructor SQLite, sau BEGIN IMMEDIATE của phần nâng cấp.
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(visits)')}
        for name, definition in [('notes',"TEXT NOT NULL DEFAULT ''"), ('review_note',"TEXT NOT NULL DEFAULT ''"),
                                 ('origin',"TEXT NOT NULL DEFAULT 'camera'"), ('edit_version','INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns: conn.execute(f'ALTER TABLE visits ADD COLUMN {name} {definition}')
        conn.execute('''CREATE TABLE IF NOT EXISTS record_changes (
            id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id),
            action TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_record_changes_visit ON record_changes(visit_id,created_at)')
        conn.execute('''CREATE TABLE IF NOT EXISTS visit_attachments (
            id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id),
            direction TEXT NOT NULL, file_name TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL, notes TEXT NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_visit_attachments_visit ON visit_attachments(visit_id,direction)')

    def _query_filters(self, filters):
        filters = filters or {}
        allowed = {'plate','day','month','from','to','gate','entry_camera','exit_camera','status','review','date_basis'}
        if set(filters)-allowed: raise ValueError('Bộ lọc không hợp lệ.')
        conditions, params = [], []
        plate = re.sub(r'[\s.\-]','',str(filters.get('plate','')).upper())
        if plate:
            if not re.fullmatch('[A-Z0-9]{1,16}',plate): raise ValueError('Bộ lọc biển số chỉ gồm chữ và số.')
            conditions.append('v.plate LIKE ?'); params.append('%'+plate+'%')
        for field in ('gate','entry_camera','exit_camera'):
            value = filters.get(field)
            if value:
                if value not in CAMERAS: raise ValueError('Cổng lọc không hợp lệ.')
                if field == 'gate': conditions.append('(v.entry_camera=? OR v.exit_camera=?)'); params.extend((value,value))
                else: conditions.append(f'v.{field}=?'); params.append(value)
        state = filters.get('status')
        if state:
            if state not in STATES: raise ValueError('Trạng thái lọc không hợp lệ.')
            conditions.append('v.status=?'); params.append(state)
        if filters.get('review') not in (None,'','0','1'): raise ValueError('Bộ lọc cần kiểm tra không hợp lệ.')
        if filters.get('review') == '1': conditions.append(_review_sql())
        basis = filters.get('date_basis') or 'any'
        if basis not in ('any','entry','exit'): raise ValueError('Loại ngày giờ lọc không hợp lệ.')
        if filters.get('day') and filters.get('month'): raise ValueError('Chọn ngày hoặc tháng, không chọn cả hai.')
        periods = []
        local = timezone(timedelta(hours=7))
        if filters.get('day'):
            day = date.fromisoformat(filters['day'])
            start = datetime.combine(day,datetime.min.time(),tzinfo=local)
            periods.append((_utc(start),_utc(start+timedelta(days=1))))
        if filters.get('month'):
            if not re.fullmatch(r'\d{4}-\d{2}',filters['month']): raise ValueError('Tháng không hợp lệ.')
            start = datetime.fromisoformat(filters['month']+'-01').replace(tzinfo=local)
            end = start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
            periods.append((_utc(start),_utc(end)))
        start = _utc(filters['from']) if filters.get('from') else None
        end = _utc(filters['to']) if filters.get('to') else None
        if start and end and start >= end: raise ValueError('Thời điểm đến phải sau thời điểm từ.')
        if start or end: periods.append((start,end))
        for start,end in periods:
            expressions = []
            fields = ['v.entered_at','v.exited_at','v.created_at'] if basis=='any' else ['v.entered_at' if basis=='entry' else 'v.exited_at']
            for field in fields:
                parts = []
                if field=='v.created_at': parts.append('v.entered_at IS NULL AND v.exited_at IS NULL')
                if start: parts.append(field+'>=?'); params.append(start)
                if end: parts.append(field+'<?'); params.append(end)
                expressions.append('('+' AND '.join(parts)+')')
            conditions.append('('+' OR '.join(expressions)+')')
        return conditions,params

    def management_list(self, offset=0, limit=30, deleted=False, filters=None, inside=False):
        if offset < 0 or not 1 <= limit <= 100: raise ValueError('Tham số hồ sơ không hợp lệ.')
        conditions,params = self._query_filters(filters)
        conditions.insert(0,"v.realm='real'")
        if inside:
            # Bao gồm lượt đã ẩn: xóa hồ sơ không có nghĩa là xe đã ra.
            conditions.append("v.status='INSIDE' AND v.exited_at IS NULL")
        else: conditions.append('v.deleted_at IS NOT NULL' if deleted else 'v.deleted_at IS NULL')
        where = ' AND '.join(conditions)
        with self.connection() as conn:
            conn.execute('BEGIN')
            total = conn.execute(f'SELECT COUNT(*) FROM visits v WHERE {where}',params).fetchone()[0]
            rows = [dict(row) for row in conn.execute(f'''SELECT v.*,
                CASE WHEN {_photo_sql('entry')} THEN 1 ELSE 0 END AS has_entry_photo,
                CASE WHEN {_photo_sql('exit')} THEN 1 ELSE 0 END AS has_exit_photo
                FROM visits v WHERE {where}
                ORDER BY CASE WHEN v.status='INSIDE' THEN 0 ELSE 1 END,COALESCE(v.exited_at,v.entered_at,v.created_at) DESC,v.id DESC LIMIT ? OFFSET ?''',(*params,limit,offset))]
            for row in rows:
                row.pop('realm',None); row.pop('entry_source',None)
                row['review_reasons'] = review_reasons(row); row['needs_review'] = bool(row['review_reasons'])
                row['evidence_count'] = conn.execute('SELECT COUNT(*) FROM passages WHERE visit_id=?',(row['id'],)).fetchone()[0]
                row['attachment_count'] = conn.execute('SELECT COUNT(*) FROM visit_attachments WHERE visit_id=?',(row['id'],)).fetchone()[0]
            counts = dict(conn.execute(f'SELECT v.status,COUNT(*) FROM visits v WHERE {where} GROUP BY v.status',params).fetchall())
            # Xe đã có giờ ra vẫn là lượt đã rời D, dù thiếu thông tin vào.
            closed_count = conn.execute(f'SELECT COUNT(*) FROM visits v WHERE {where} AND v.exited_at IS NOT NULL',params).fetchone()[0]
            review_count = conn.execute(f'SELECT COUNT(*) FROM visits v WHERE {where} AND {_review_sql()}',params).fetchone()[0]
            occupancy = self._occupancy(conn)
        return dict(items=rows,total=total,offset=offset,limit=limit,deleted=deleted,inside=inside,
                    occupancy=occupancy,counts=counts,closed_count=closed_count,review_count=review_count,storage=self.storage_info())

    def list_inside(self, offset=0, limit=30, filters=None):
        return self.management_list(offset,limit,False,filters,inside=True)

    def _management_detail(self, conn, visit):
        visit['attachments'] = [dict(row) for row in conn.execute('SELECT * FROM visit_attachments WHERE visit_id=? ORDER BY created_at,id',(visit['id'],))]
        visit['changes'] = [dict(row) for row in conn.execute('SELECT * FROM record_changes WHERE visit_id=? ORDER BY created_at,id',(visit['id'],))]
        for change in visit['changes']:
            change['before'] = json.loads(change.pop('before_json')); change['after'] = json.loads(change.pop('after_json'))
        for direction in ('entry','exit'):
            visit['has_'+direction+'_photo'] = any(p['direction']==direction for p in visit['passages']) or any(a['direction']==direction for a in visit['attachments'])
        visit['review_reasons'] = review_reasons(visit); visit['needs_review'] = bool(visit['review_reasons'])
        return visit

    @staticmethod
    def _manual_values(payload, previous=None):
        values = {}
        values['plate'] = re.sub(r'[\s.\-]','',str(payload.get('plate','')).upper())
        if not re.fullmatch(r'\d{2}[A-Z]{1,2}\d{4,6}',values['plate']): raise ValueError('Biển số không hợp lệ; ví dụ 51K12345.')
        now = datetime.now(timezone.utc)
        for field in ('entered_at','exited_at'):
            values[field] = _utc(payload[field]) if payload.get(field) else None
            if values[field] and datetime.fromisoformat(values[field]) > now: raise ValueError('Ngày giờ vào/ra không được ở tương lai.')
        if values['entered_at'] and values['exited_at'] and values['entered_at'] >= values['exited_at']:
            raise ValueError('Giờ ra phải sau giờ vào.')
        for field in ('entry_camera','exit_camera'):
            values[field] = payload.get(field) or None
            if values[field] and values[field] not in CAMERAS: raise ValueError('Cổng không hợp lệ.')
        for field in ('notes','review_note'):
            values[field] = str(payload.get(field,'')).strip()
            if len(values[field]) > 1000: raise ValueError('Ghi chú tối đa 1000 ký tự.')
        if not values['entered_at'] and not values['exited_at']:
            if previous and previous['status']=='WAITING': values['status']='WAITING'
            else: raise ValueError('Cần ít nhất một thời điểm vào hoặc ra để bổ sung lượt thiếu.')
        elif not values['exited_at']:
            # Giờ vào hồ sơ của xe chờ đã có từ lần thấy đầu. Sửa ghi chú
            # không có nghĩa là cấp quyền cho xe đó vào D.
            unchanged_waiting = previous and previous['status']=='WAITING' and all(values[k]==previous[k] for k in ('entered_at','entry_camera'))
            values['status']='WAITING' if unchanged_waiting else 'INSIDE'
        elif all(values[field] for field in ('entered_at','entry_camera','exit_camera')): values['status']='COMPLETE'
        else: values['status']='NEEDS_REVIEW'
        reason = str(payload.get('reason','')).strip()
        if not 5 <= len(reason) <= 500: raise ValueError('Cần lý do nhập/sửa từ 5 đến 500 ký tự.')
        return values,reason

    @staticmethod
    def _prepare_attachments(items):
        if not isinstance(items,list) or len(items)>4: raise ValueError('Mỗi lần đính kèm tối đa 4 ảnh.')
        prepared = []
        for item in items:
            if not isinstance(item,dict) or item.get('direction') not in ('entry','exit','other'):
                raise ValueError('Cần chọn ảnh vào, ảnh ra hoặc ảnh bổ sung.')
            encoded = item.get('data','')
            if not isinstance(encoded,str) or len(encoded)>2_800_000: raise ValueError('Mỗi ảnh tối đa 2 MB.')
            try:
                data = base64.b64decode(encoded.split(',',1)[-1],validate=True)
                if not 4 <= len(data) <= 2_000_000: raise ValueError('Ảnh quá lớn hoặc rỗng.')
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    extension = {'JPEG':'jpg','PNG':'png','WEBP':'webp'}.get(image.format)
                    if not extension or image.width*image.height>20_000_000: raise ValueError('Định dạng/kích thước ảnh không hỗ trợ.')
                    image.verify()
            except Exception as error:
                raise ValueError('Ảnh không hợp lệ; dùng JPEG, PNG hoặc WebP tối đa 2 MB.') from error
            notes = str(item.get('notes','')).strip()
            if len(notes)>500: raise ValueError('Ghi chú ảnh tối đa 500 ký tự.')
            prepared.append((data,extension,item['direction'],notes))
        return prepared

    def save_manual(self, payload, visit_id=None):
        """Nhập/sửa tay có audit, optimistic lock và chống gửi lặp khi retry.

        Lượt chưa ra giữ D như lượt camera. Không cho tạo thêm xe trong D
        khi còn xe khác; không sửa/xóa ảnh OCR hoặc giả lập lần đọc camera.
        """
        if not isinstance(payload,dict): raise ValueError('Nội dung hồ sơ không hợp lệ.')
        attachments = self._prepare_attachments(payload.get('attachments',[]))
        created_files = []
        try:
            with self.connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                previous = conn.execute("SELECT * FROM visits WHERE id=? AND realm='real'",(visit_id,)).fetchone() if visit_id else None
                if visit_id and not previous: return None
                previous = dict(previous) if previous else None
                if previous and previous['deleted_at']: raise RecordConflict('Khôi phục hồ sơ khỏi Thùng rác trước khi chỉnh sửa.')
                if previous and (type(payload.get('expected_version')) is not int or payload['expected_version'] != previous['edit_version']):
                    raise RecordConflict('Hồ sơ vừa được camera/người khác cập nhật. Đóng và mở lại hồ sơ trước khi sửa.')
                values,reason = self._manual_values(payload,previous)
                if not previous:
                    visit_id = payload.get('request_id','')
                    if not isinstance(visit_id,str) or not re.fullmatch('[0-9a-f]{32}',visit_id): raise ValueError('Thiếu mã yêu cầu nhập tay; mở lại biểu mẫu.')
                    duplicate = conn.execute('SELECT origin FROM visits WHERE id=?',(visit_id,)).fetchone()
                    if duplicate:
                        if duplicate['origin']!='manual': raise RecordConflict('Mã yêu cầu đã được sử dụng.')
                        return dict(state='duplicate',visitIds=[visit_id],occupancy=self._occupancy(conn))
                if values['status']=='INSIDE':
                    occupied = conn.execute("SELECT id FROM visits WHERE realm='real' AND status='INSIDE' AND exited_at IS NULL AND id<>?",(visit_id,)).fetchone()
                    if occupied: raise RecordConflict('D đang có xe khác. Kiểm tra lượt chưa ra trước khi bổ sung lượt đang bên trong.')
                if values['entered_at']:
                    duplicate = conn.execute("SELECT id FROM visits WHERE realm='real' AND plate=? AND entered_at=? AND id<>?",(values['plate'],values['entered_at'],visit_id)).fetchone()
                    if duplicate: raise RecordConflict('Đã có lượt của biển số này tại cùng thời điểm vào; hãy sửa hồ sơ hiện có.')
                now = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
                if previous:
                    columns = ','.join(field+'=?' for field in EDIT_FIELDS)
                    conn.execute(f'UPDATE visits SET {columns},edit_version=edit_version+1 WHERE id=?',(*[values[k] for k in EDIT_FIELDS],visit_id))
                    if previous['status']=='WAITING' and values['status']!='WAITING':
                        conn.execute('UPDATE visits SET issue=NULL WHERE id=?',(visit_id,))
                    if previous['status'] in ('INSIDE','WAITING') and values['exited_at']:
                        for plate in {previous['plate'],values['plate']}:
                            conn.execute('INSERT OR REPLACE INTO plate_release VALUES (?,?)',(plate,now))
                else:
                    conn.execute('''INSERT INTO visits(id,realm,plate,entered_at,exited_at,entry_camera,exit_camera,status,created_at,notes,review_note,origin,edit_version)
                        VALUES (?,'real',?,?,?,?,?,?,?,?,?,'manual',0)''',
                        (visit_id,values['plate'],values['entered_at'],values['exited_at'],values['entry_camera'],values['exit_camera'],values['status'],now,values['notes'],values['review_note']))
                for data,extension,direction,notes in attachments:
                    identifier = uuid.uuid4().hex; name = identifier+'_upload.'+extension
                    path = self.images/name
                    with path.open('xb') as output:
                        created_files.append(path); output.write(data); output.flush(); os.fsync(output.fileno())
                    conn.execute('INSERT INTO visit_attachments(id,visit_id,direction,file_name,sha256,created_at,notes) VALUES (?,?,?,?,?,?,?)',
                                 (identifier,visit_id,direction,name,hashlib.sha256(data).hexdigest(),now,notes))
                before = {k:previous[k] for k in EDIT_FIELDS} if previous else {}
                conn.execute('INSERT INTO record_changes(id,visit_id,action,created_at,reason,before_json,after_json) VALUES (?,?,?,?,?,?,?)',
                             (uuid.uuid4().hex,visit_id,'manual-edit' if previous else 'manual-create',now,reason,
                              json.dumps(before,ensure_ascii=False),json.dumps(values,ensure_ascii=False)))
                conn.execute('UPDATE gate_state SET revision=revision+1 WHERE id=1')
                return dict(state='manual-edited' if previous else 'manual-created',visitIds=[visit_id],occupancy=self._occupancy(conn))
        except BaseException:
            # Chỉ dọn file mới thuộc transaction lỗi, không đụng ảnh đã lưu.
            for path in created_files: path.unlink(missing_ok=True)
            raise
