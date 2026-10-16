"""Hồ sơ xe thật: camera đầu tiên ghi vào, camera khác cùng biển số ghi ra.

Database là nguồn trạng thái D duy nhất. Không dùng kịch bản, hướng/vạch camera,
không tự tạo biển số. Xóa/khôi phục hồ sơ chỉ đổi danh sách, không đổi D. Module chỉ
nhận kết quả YOLO → PaddleOCR đã được xác nhận trên nhiều khung RTSP thật.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import math
import os
import re
import sqlite3
import threading
import uuid
from record_management import RecordManagementMixin

CAMERAS = ("A", "B", "C", "E")
GATE_LOGIC_VERSION = 5
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "Data" / "vehicle-records"


def utc_time(value):
    """Lưu UTC có múi giờ; giao diện đổi sang giờ Việt Nam (UTC+7)."""
    date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Ngày giờ phải có múi giờ.")
    return date.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class VisitStore(RecordManagementMixin):
    """Một transaction xử lý nhận diện, cập nhật D và ghi hai ảnh bằng chứng."""
    def __init__(self, data_dir=DEFAULT_DATA_DIR):
        self.root = Path(data_dir).resolve()
        self.images = self.root / "evidence"
        self.images.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "visits.sqlite3"
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            # Giữ cột realm của DB cũ để nâng cấp không mất hồ sơ. Tất cả lượt
            # mới và API hiện tại chỉ dùng 'real'; không có chế độ dữ liệu thử.
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS visits (
                    id TEXT PRIMARY KEY, realm TEXT NOT NULL DEFAULT 'real',
                    plate TEXT NOT NULL, entered_at TEXT, exited_at TEXT,
                    entry_camera TEXT, exit_camera TEXT, status TEXT NOT NULL,
                    issue TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_visits_open ON visits(realm,plate,exited_at);
                CREATE INDEX IF NOT EXISTS ix_visits_date ON visits(realm,created_at DESC);
                CREATE TABLE IF NOT EXISTS passages (
                    id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id),
                    event_key TEXT UNIQUE NOT NULL, direction TEXT NOT NULL,
                    camera_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    source_kind TEXT NOT NULL, source_name TEXT NOT NULL,
                    media_time REAL NOT NULL, confidence REAL NOT NULL,
                    scene_file TEXT NOT NULL, crop_file TEXT NOT NULL,
                    scene_sha256 TEXT NOT NULL, crop_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gate_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO gate_state(id) VALUES (1);
                CREATE TABLE IF NOT EXISTS plate_release (
                    plate TEXT PRIMARY KEY, last_seen_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS visit_corrections (
                    id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id),
                    action TEXT NOT NULL, camera_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL, reason TEXT NOT NULL);
            """)
            # Serialize nâng cấp schema khi OCR và API cùng khởi động.
            conn.execute("BEGIN IMMEDIATE")
            # Mã nguồn chỉ lưu hash nhận diện nguồn (không chứa RTSP/mật khẩu).
            if "entry_source" not in {row["name"] for row in conn.execute("PRAGMA table_info(visits)")}:
                conn.execute("ALTER TABLE visits ADD COLUMN entry_source TEXT")
            if "deleted_at" not in {row["name"] for row in conn.execute("PRAGMA table_info(visits)")}:
                # Xóa có khôi phục: ảnh và lượt vẫn tồn tại để không mất bằng
                # chứng hay nhầm rằng xe chưa ra đã rời vùng D.
                conn.execute("ALTER TABLE visits ADD COLUMN deleted_at TEXT")
            self._management_columns(conn)

    @contextmanager
    def connection(self):
        """Connection riêng mỗi luồng; WAL cho phép giao diện đọc trong lúc OCR ghi."""
        conn = sqlite3.connect(self.database, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def storage_info(self):
        return {'backend':'sqlite','database':str(self.database)}

    @staticmethod
    def jpeg(data):
        if not isinstance(data, bytes) or not 4 <= len(data) <= 2_000_000 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise ValueError("Ảnh bằng chứng JPEG không hợp lệ.")
        return data

    @staticmethod
    def _occupancy(conn):
        """D có xe iff còn lượt INSIDE. WAITING có giờ hồ sơ nhưng chưa chiếm D."""
        rows = conn.execute("SELECT id,plate,entry_camera,entered_at,entry_source,deleted_at FROM visits WHERE realm='real' AND status='INSIDE' AND exited_at IS NULL").fetchall()
        revision = conn.execute("SELECT revision FROM gate_state WHERE id=1").fetchone()[0]
        # DB cũ có nhiều lượt mở: giữ đèn đỏ, báo cần kiểm tra, không đoán xe ra.
        last_exit = conn.execute("""SELECT id,plate,entry_camera,exit_camera,entered_at,exited_at
                                   FROM visits WHERE realm='real' AND status IN ('COMPLETE','NEEDS_REVIEW') AND exited_at IS NOT NULL
                                   ORDER BY exited_at DESC,id DESC LIMIT 1""").fetchone()
        # Quyền xanh thuộc xe đang giữ D; khi D trống, lấy xe chờ sớm nhất.
        # Không loại lượt đã ẩn: xóa hồ sơ không chứng minh xe đã rời hàng chờ.
        next_row = conn.execute("""SELECT id,plate,entry_camera,entered_at,created_at FROM visits
            WHERE realm='real' AND status='WAITING' AND exited_at IS NULL
            ORDER BY created_at,entry_camera,id LIMIT 1""").fetchone()
        next_vehicle = dict(next_row) if next_row else None
        priority = dict(rows[0]) if len(rows)==1 else next_vehicle if not rows else None
        # Chỉ A/B có đèn. Không đoán C/E tương ứng hướng A hoặc B.
        green_gate = priority['entry_camera'] if priority and priority['entry_camera'] in ('A','B') else None
        return {"occupied": bool(rows), "vehicle": dict(rows[0]) if len(rows) == 1 else None,
                "lastExit": dict(last_exit) if last_exit else None,
                "nextVehicle": next_vehicle, "greenGate": green_gate,
                "signals": {gate:'GREEN' if gate==green_gate else 'RED' for gate in ('A','B')},
                "signalPolicyVersion": 1,
                "revision": revision, "issue": "Có nhiều lượt chưa ra; cần đối chiếu hồ sơ" if len(rows) > 1 else None,
                "waitingCount": conn.execute("SELECT COUNT(*) FROM visits WHERE realm='real' AND status='WAITING'").fetchone()[0]}

    def occupancy(self):
        with self.connection() as conn:
            conn.execute("BEGIN")  # Một snapshot nhất quán giữa danh sách và revision.
            return self._occupancy(conn)

    def record(self, event):
        """Lần đọc đầu là giờ/cổng vào; camera khác cùng biển là giờ/cổng ra.

        Hồ sơ và quyền chiếm D là hai việc riêng: xe được thấy khi D bận
        vẫn có giờ vào hồ sơ, nhưng trạng thái WAITING không chiếm D. Khi
        camera khác thấy xe đó, đóng đúng lượt của nó; không giải phóng D
        của xe khác. Quyền chiếm D tiếp theo theo FIFO; chỉ A hoặc B được
        xanh khi đúng hướng ưu tiên. Hai hướng không được cùng xanh.
        """
        plate, camera = event["plate"], event["camera_id"]
        if camera not in CAMERAS or not re.fullmatch(r"\d{2}[A-Z]{1,2}\d{4,6}", plate) or event["source_kind"] != "live":
            raise ValueError("Sự kiện camera thật chưa hợp lệ.")
        at = utc_time(event["occurred_at"])
        confidence, media_time = float(event["confidence"]), float(event["media_time"])
        if not math.isfinite(confidence) or confidence < .7 or confidence > 1 or not math.isfinite(media_time):
            raise ValueError("Kết quả OCR chưa đủ độ tin cậy.")
        source_id = event.get("source_fingerprint")
        if source_id and not re.fullmatch(r"[0-9a-f]{64}", source_id):
            raise ValueError("Mã nguồn camera không hợp lệ.")
        created_files = []
        try:
            with self.connection() as conn:
                # Cùng khóa/transaction cho mọi camera và mọi worker.
                conn.execute("BEGIN IMMEDIATE")
                prior = conn.execute("SELECT visit_id,direction FROM passages WHERE event_key=?", (event["event_key"],)).fetchone()
                if prior:
                    return {"state": "duplicate", "visitIds": [], "occupancy": self._occupancy(conn)}
                current = self._occupancy(conn)
                if current["issue"]:
                    return {"state": "needs-review", "visitIds": [], "occupancy": current}
                released = conn.execute("SELECT last_seen_at FROM plate_release WHERE plate=?", (plate,)).fetchone()
                if released:
                    gap = (datetime.fromisoformat(at) - datetime.fromisoformat(released[0])).total_seconds()
                    if gap < 5:
                        # OCR còn trong khung sau lúc ra không tạo lại lượt vào.
                        conn.execute("UPDATE plate_release SET last_seen_at=MAX(last_seen_at,?) WHERE plate=?", (at, plate))
                        return {"state": "already-exited", "visitIds": [], "occupancy": current}
                    conn.execute("DELETE FROM plate_release WHERE plate=?", (plate,))

                # Ghép theo biển số của TỪNG lượt, không chỉ xe đang giữ D.
                opened = conn.execute("""SELECT * FROM visits WHERE realm='real' AND plate=?
                    AND status IN ('INSIDE','WAITING') AND exited_at IS NULL
                    ORDER BY created_at,id LIMIT 1""", (plate,)).fetchone()
                if opened:
                    first_at = opened['entered_at'] or opened['created_at']
                    waiting = opened['status']=='WAITING'
                    if at <= first_at:
                        return {"state": "waiting" if waiting else "already-inside", "visitIds": [], "occupancy": current}
                    if camera != opened['entry_camera']:
                        # ID cổng quyết định lượt ra, không phải URL RTSP.
                        action, visit_id = 'exit', opened['id']
                    elif waiting and not current['occupied']:
                        last_exit=conn.execute("SELECT MAX(exited_at) FROM visits WHERE realm='real'").fetchone()[0]
                        if last_exit and at <= last_exit:
                            return {"state":"stale-frame","visitIds":[],"occupancy":current}
                        if current['nextVehicle']['id'] != opened['id']:
                            # D đã trống nhưng xe này đến sau: giữ trong hàng
                            # chờ, không lấy D trước xe đang được ưu tiên xanh.
                            return {"state":"waiting","visitIds":[],"occupancy":current}
                        # D vừa trống, xe chờ được đọc mới tại cổng ban đầu.
                        # Chỉ cấp quyền vào D; KHÔNG đổi giờ vào hồ sơ ban đầu,
                        # không nhân đôi ảnh hoặc lượt bởi các khung lặp.
                        conn.execute("""UPDATE visits SET status='INSIDE',entered_at=?,issue=NULL,
                            deleted_at=NULL,edit_version=edit_version+1 WHERE id=?""",(first_at,opened['id']))
                        conn.execute('UPDATE gate_state SET revision=revision+1 WHERE id=1')
                        return {'state':'entry','visitIds':[opened['id']],'occupancy':self._occupancy(conn)}
                    else:
                        return {"state":"waiting" if waiting else "already-inside","visitIds":[],"occupancy":current}
                else:
                    last_exit=conn.execute("SELECT MAX(exited_at) FROM visits WHERE realm='real'").fetchone()[0]
                    if last_exit and at <= last_exit:
                        return {"state":"stale-frame","visitIds":[],"occupancy":current}
                    # Một camera vừa đọc xe mới không được chen trước lượt chờ
                    # cũ, kể cả khi worker của nó trả kết quả sớm hơn.
                    action, visit_id = ('waiting' if current['occupied'] or current['nextVehicle'] else 'entry'), uuid.uuid4().hex

                # Ảnh gốc/crop cùng khung xác nhận, ghi trong cùng transaction.
                scene, crop = self.jpeg(event["scene"]), self.jpeg(event["crop"])
                passage_id = uuid.uuid4().hex
                scene_name, crop_name = f"{passage_id}_scene.jpg", f"{passage_id}_plate.jpg"
                for filename, image in ((scene_name, scene), (crop_name, crop)):
                    target = self.images / filename
                    with target.open("xb") as stream:
                        created_files.append(target)
                        stream.write(image); stream.flush(); os.fsync(stream.fileno())
                if action == 'exit':
                    conn.execute("""UPDATE visits SET entered_at=?,exited_at=?,exit_camera=?,status='COMPLETE',issue=NULL,
                        deleted_at=?,edit_version=edit_version+1 WHERE id=?""",
                        (first_at,at,camera,None if waiting else opened['deleted_at'],visit_id))
                    conn.execute('INSERT OR REPLACE INTO plate_release VALUES (?,?)',(plate,at))
                else:
                    # Kể cả xe chờ cũng lấy ngay thời điểm nhận diện đầu làm
                    # giờ vào hồ sơ theo quy tắc người vận hành đã chốt.
                    conn.execute("""INSERT INTO visits(id,realm,plate,entered_at,entry_camera,status,issue,created_at,entry_source)
                        VALUES (?,'real',?,?,?,?,?,?,?)""",
                        (visit_id,plate,at,camera,'WAITING' if action=='waiting' else 'INSIDE',
                         'Chờ ngoài D' if action=='waiting' else None,at,source_id))
                conn.execute("""INSERT INTO passages(id,visit_id,event_key,direction,camera_id,occurred_at,source_kind,source_name,media_time,
                    confidence,scene_file,crop_file,scene_sha256,crop_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (passage_id,visit_id,event['event_key'],'exit' if action=='exit' else 'entry',camera,at,'live',f'Camera {camera}',
                     media_time,confidence,scene_name,crop_name,hashlib.sha256(scene).hexdigest(),hashlib.sha256(crop).hexdigest()))
                conn.execute('UPDATE gate_state SET revision=revision+1 WHERE id=1')
                return {'state':action,'visitIds':[visit_id],'occupancy':self._occupancy(conn)}
        except Exception:
            for filename in created_files:
                filename.unlink(missing_ok=True)
            raise

    def confirm_exit(self, visit_id, camera, occurred_at, reason):
        """Ngoại lệ: người vận hành xác nhận xe đã ra khi thiếu lần đọc.

        Không giả lập camera, không tạo ảnh ra. Lưu lý do/thời điểm xác nhận
        riêng để phân biệt với sự kiện YOLO/Paddle. Chỉ đóng đúng lượt INSIDE.
        """
        at = utc_time(occurred_at)
        now = datetime.now(timezone.utc)
        reason = str(reason).strip()
        if camera not in CAMERAS or not 5 <= len(reason) <= 500 or datetime.fromisoformat(at) > now:
            raise ValueError("Cần cổng ra, thời gian không ở tương lai và lý do ít nhất 5 ký tự.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM visits WHERE id=? AND realm='real'", (visit_id,)).fetchone()
            if not row:
                return None
            if row['status'] != 'INSIDE' or row['exited_at'] or not row['entered_at'] or at <= row['entered_at']:
                raise ValueError("Chỉ xác nhận lượt đang trong D; giờ ra phải sau giờ vào.")
            confirmed_at = now.isoformat(timespec='milliseconds')
            conn.execute("INSERT INTO visit_corrections VALUES (?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, visit_id, 'confirm-exit', camera, at, confirmed_at, reason))
            conn.execute("UPDATE visits SET exited_at=?,exit_camera=?,status='COMPLETE',issue=?,edit_version=edit_version+1 WHERE id=?",
                         (at, camera, 'Xe ra được xác nhận thủ công; không có ảnh camera ra.', visit_id))
            # Chống OCR còn trong khung tái tạo lượt ngay sau thao tác xác nhận.
            conn.execute("INSERT OR REPLACE INTO plate_release VALUES (?,?)", (row['plate'], confirmed_at))
            conn.execute("UPDATE gate_state SET revision=revision+1 WHERE id=1")
            return {"state": "manual-exit", "visitIds": [visit_id], "occupancy": self._occupancy(conn)}

    def set_deleted(self, visit_id, deleted):
        """Chuyển vào/ra Thùng rác, tuyệt đối không đổi trạng thái xe/đèn.

        Lượt đang INSIDE vẫn tham gia _occupancy và được ghép với lần đọc ở
        camera ra. Xóa hồ sơ không thay cho sự kiện xe ra, không xóa ảnh vật lý.
        Khôi phục hiển thị trạng thái mới nhất, kể cả xe đã ra trong lúc ẩn.
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id,deleted_at FROM visits WHERE id=? AND realm='real'", (visit_id,)).fetchone()
            if not row:
                return None
            if bool(row["deleted_at"]) != deleted:
                at = datetime.now(timezone.utc).isoformat(timespec="milliseconds") if deleted else None
                conn.execute("UPDATE visits SET deleted_at=?,edit_version=edit_version+1 WHERE id=?", (at, visit_id))
                # Revision đổi để phản hồi cũ không ghi đè thông báo D vừa nhận.
                conn.execute("UPDATE gate_state SET revision=revision+1 WHERE id=1")
            return {"state": "trashed" if deleted else "restored", "id": visit_id,
                    "visitIds": [], "occupancy": self._occupancy(conn)}

    def list_visits(self, offset=0, limit=30, deleted=False, filters=None):
        return self.management_list(offset,limit,deleted,filters)

    def detail(self, visit_id):
        with self.connection() as conn:
            conn.execute('BEGIN')
            row = conn.execute("SELECT * FROM visits WHERE id=? AND realm='real'", (visit_id,)).fetchone()
            if not row:
                return None
            visit = dict(row); visit.pop("realm", None); visit.pop("entry_source", None)
            visit["passages"] = [dict(item) for item in conn.execute("SELECT * FROM passages WHERE visit_id=? ORDER BY occurred_at", (visit_id,))]
            visit["corrections"] = [dict(item) for item in conn.execute("SELECT * FROM visit_corrections WHERE visit_id=? ORDER BY confirmed_at", (visit_id,))]
            return self._management_detail(conn,visit)

    def evidence(self, filename):
        """Chỉ đọc tên ảnh có trong hồ sơ thật, chặn đường dẫn tùy ý."""
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            return None
        with self.connection() as conn:
            row = conn.execute("SELECT 1 FROM passages p JOIN visits v ON v.id=p.visit_id WHERE v.realm='real' AND (p.scene_file=? OR p.crop_file=?)", (filename, filename)).fetchone()
            if not row:
                row = conn.execute("SELECT 1 FROM visit_attachments a JOIN visits v ON v.id=a.visit_id WHERE v.realm='real' AND a.file_name=?",(filename,)).fetchone()
        file = self.images / filename
        return file if row and file.is_file() else None


class PassageRecorder:
    """Adapter OCR: chỉ nhận RTSP thật, không thay đổi YOLO/Paddle/tracker."""
    def __init__(self, store):
        self.store = store
        self.frames = {}
        self.lock = threading.Lock()

    def observe(self, *, camera_id, source_key, engine_session, detections,
                frame_id, media_time, captured_at, width, height, scene,
                source_kind, source_name, real_source=False, scene_cut=False,
                source_fingerprint=None):
        if source_kind != "live" or not real_source:
            return {"state": "preview-only", "visitIds": []}
        results = []
        with self.lock:
            key = (camera_id, source_key, engine_session)
            # Frame lặp hoặc đến sai thứ tự không được tạo sự kiện lần hai.
            identifier = int(frame_id)
            if identifier <= self.frames.get(key, -1):
                return {"state": "duplicate-frame", "visitIds": []}
            for detection in detections:
                if not (detection.get("confirmed") and not detection.get("changedConfirmed") and
                        detection.get("evidenceCount", 0) >= 3 and detection.get("confidence", 0) >= .7 and detection.get("plate")):
                    continue
                uri = detection.get("crop", "")
                if not uri.startswith("data:image/jpeg;base64,"):
                    continue
                result = self.store.record({"plate": detection["plate"], "camera_id": camera_id,
                    "occurred_at": captured_at, "event_key": f"{camera_id}:{engine_session}:{source_key}:{frame_id}:{detection['plate']}",
                    "source_kind": "live", "source_fingerprint": source_fingerprint, "media_time": media_time,
                    "confidence": detection["confidence"], "scene": scene,
                    "crop": base64.b64decode(uri.split(",", 1)[1], validate=True)})
                results.append(result)
            # Chỉ đánh dấu frame sau khi toàn bộ transaction thành công;
            # nếu ổ lưu lỗi, frame có thể retry mà không mất sự kiện.
            self.frames[key] = identifier
            if len(self.frames) > 256:
                del self.frames[next(iter(self.frames))]
        return {"state": results[-1]["state"] if results else "waiting-confirmation",
                "visitIds": [visit for result in results for visit in result["visitIds"]],
                "occupancy": self.store.occupancy()}
