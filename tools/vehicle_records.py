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

CAMERAS = ("A", "B", "C", "E")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "Data" / "vehicle-records"


def utc_time(value):
    """Lưu UTC có múi giờ; giao diện đổi sang giờ Việt Nam (UTC+7)."""
    date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Ngày giờ phải có múi giờ.")
    return date.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class VisitStore:
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
        """D có xe iff còn lượt INSIDE. WAITING không chiếm D, không tính giờ vào."""
        rows = conn.execute("SELECT id,plate,entry_camera,entered_at,entry_source,deleted_at FROM visits WHERE realm='real' AND status='INSIDE' AND exited_at IS NULL").fetchall()
        revision = conn.execute("SELECT revision FROM gate_state WHERE id=1").fetchone()[0]
        # DB cũ có nhiều lượt mở: giữ đèn đỏ, báo cần kiểm tra, không đoán xe ra.
        last_exit = conn.execute("""SELECT id,plate,entry_camera,exit_camera,entered_at,exited_at
                                   FROM visits WHERE realm='real' AND status='COMPLETE' AND exited_at IS NOT NULL
                                   ORDER BY exited_at DESC,id DESC LIMIT 1""").fetchone()
        return {"occupied": bool(rows), "vehicle": dict(rows[0]) if len(rows) == 1 else None,
                "lastExit": dict(last_exit) if last_exit else None,
                "revision": revision, "issue": "Có nhiều lượt chưa ra; cần đối chiếu hồ sơ" if len(rows) > 1 else None,
                "waitingCount": conn.execute("SELECT COUNT(*) FROM visits WHERE realm='real' AND status='WAITING'").fetchone()[0]}

    def occupancy(self):
        with self.connection() as conn:
            conn.execute("BEGIN")  # Một snapshot nhất quán giữa danh sách và revision.
            return self._occupancy(conn)

    def record(self, event):
        """Chỉ gọi từ worker sau khi xác nhận biển số trên RTSP riêng.

        Khi D trống: camera đầu tiên → INSIDE và tất cả đèn đỏ.
        Khi D có xe: cùng biển ở camera vào → bỏ lần lặp; khác biển → WAITING;
        cùng biển ở camera khác, thời điểm mới hơn → COMPLETE, D trống.
        Không tự cho xe chờ vào khi D vừa trống: cần camera đọc lại khung mới.
        Sau khi ra, chỉ tạo lượt mới nếu biển đã ngừng xuất hiện ít nhất 5 giây.
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
                # Serialize quyết định từ mọi camera, kể cả nhiều worker/process.
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
                        # Lưu suppression qua restart. Không lấy OCR còn tồn tại
                        # trong khung sau lúc ra làm một lượt vào mới.
                        conn.execute("UPDATE plate_release SET last_seen_at=MAX(last_seen_at,?) WHERE plate=?", (at, plate))
                        return {"state": "already-exited", "visitIds": [], "occupancy": current}
                    conn.execute("DELETE FROM plate_release WHERE plate=?", (plate,))
                vehicle = current["vehicle"]
                waiting = conn.execute("SELECT id,created_at FROM visits WHERE realm='real' AND plate=? AND status='WAITING' ORDER BY created_at LIMIT 1", (plate,)).fetchone()
                if vehicle:
                    if plate == vehicle["plate"]:
                        if camera == vehicle["entry_camera"] or at <= vehicle["entered_at"]:
                            return {"state": "already-inside", "visitIds": [], "occupancy": current}
                        # Cổng được nhận diện bằng camera_id, không phải URL.
                        # Người vận hành có thể dùng cùng RTSP để thử A -> B;
                        # B vẫn là cổng ra khi xác nhận cùng biển ở khung mới.
                        # Hash nguồn chỉ giữ làm metadata, không chặn lượt ra.
                        action, visit_id = "exit", vehicle["id"]
                    elif waiting:
                        return {"state": "waiting", "visitIds": [], "occupancy": current}
                    else:
                        action, visit_id = "waiting", uuid.uuid4().hex
                else:
                    action, visit_id = "entry", waiting["id"] if waiting else uuid.uuid4().hex
                    # Không dùng khung đến trễ trước thời điểm D được giải phóng.
                    last_exit = conn.execute("SELECT MAX(exited_at) FROM visits WHERE realm='real'").fetchone()[0]
                    if last_exit and at <= last_exit:
                        return {"state": "stale-frame", "visitIds": [], "occupancy": current}

                # Chỉ tạo ảnh khi có sự kiện thật mới. Ảnh toàn cảnh và ảnh crop
                # thuộc cùng khung xác nhận, không lấy ảnh mẫu/hình thay thế.
                scene, crop = self.jpeg(event["scene"]), self.jpeg(event["crop"])
                passage_id = uuid.uuid4().hex
                scene_name, crop_name = f"{passage_id}_scene.jpg", f"{passage_id}_plate.jpg"
                for filename, image in ((scene_name, scene), (crop_name, crop)):
                    target = self.images / filename
                    with target.open("xb") as stream:
                        created_files.append(target)
                        stream.write(image); stream.flush(); os.fsync(stream.fileno())
                if action == "exit":
                    conn.execute("UPDATE visits SET exited_at=?,exit_camera=?,status='COMPLETE' WHERE id=?", (at, camera, visit_id))
                    conn.execute("INSERT OR REPLACE INTO plate_release VALUES (?,?)", (plate, at))
                elif action == "entry" and waiting:
                    # Đây là sự kiện vào mới sau khi D trống; đưa lượt chờ đã
                    # bị ẩn trở lại danh sách để không mất lượt đang trong D.
                    conn.execute("UPDATE visits SET entered_at=?,entry_camera=?,entry_source=?,status='INSIDE',issue=NULL,deleted_at=NULL WHERE id=?", (at, camera, source_id, visit_id))
                else:
                    conn.execute("""INSERT INTO visits(id,realm,plate,entered_at,entry_camera,status,issue,created_at,entry_source)
                                    VALUES (?,'real',?,?,?,?,?,?,?)""",
                                 (visit_id, plate, at if action == "entry" else None, camera,
                                  "INSIDE" if action == "entry" else "WAITING",
                                  None if action == "entry" else "Chờ ngoài D; chưa ghi thời gian vào", at, source_id))
                conn.execute("""INSERT INTO passages(id,visit_id,event_key,direction,camera_id,occurred_at,source_kind,source_name,media_time,
                                confidence,scene_file,crop_file,scene_sha256,crop_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (passage_id, visit_id, event["event_key"], action, camera, at, "live", f"Camera {camera}",
                              media_time, confidence, scene_name, crop_name, hashlib.sha256(scene).hexdigest(), hashlib.sha256(crop).hexdigest()))
                conn.execute("UPDATE gate_state SET revision=revision+1 WHERE id=1")
                return {"state": action, "visitIds": [visit_id], "occupancy": self._occupancy(conn)}
        except Exception:
            # Chỉ xóa file của transaction lỗi, không xóa bằng chứng đã commit.
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
            conn.execute("UPDATE visits SET exited_at=?,exit_camera=?,status='COMPLETE',issue=? WHERE id=?",
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
                conn.execute("UPDATE visits SET deleted_at=? WHERE id=?", (at, visit_id))
                # Revision đổi để phản hồi cũ không ghi đè thông báo D vừa nhận.
                conn.execute("UPDATE gate_state SET revision=revision+1 WHERE id=1")
            return {"state": "trashed" if deleted else "restored", "id": visit_id,
                    "visitIds": [], "occupancy": self._occupancy(conn)}

    def list_visits(self, offset=0, limit=30, deleted=False):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Tham số hồ sơ không hợp lệ.")
        with self.connection() as conn:
            conn.execute("BEGIN")
            condition = "deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"
            total = conn.execute(f"SELECT COUNT(*) FROM visits WHERE realm='real' AND {condition}").fetchone()[0]
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM visits WHERE realm='real' AND {condition} ORDER BY CASE WHEN status='INSIDE' THEN 0 ELSE 1 END,COALESCE(exited_at,entered_at,created_at) DESC,id DESC LIMIT ? OFFSET ?", (limit, offset))]
            for row in rows:
                row.pop("realm", None); row.pop("entry_source", None)
                row["evidence_count"] = conn.execute("SELECT COUNT(*) FROM passages WHERE visit_id=?", (row["id"],)).fetchone()[0]
            # Cùng snapshot SQLite với bảng hồ sơ, kể cả xe nằm trong Thùng rác.
            occupancy = self._occupancy(conn)
            counts = dict(conn.execute("SELECT status,COUNT(*) FROM visits WHERE realm='real' AND deleted_at IS NULL GROUP BY status").fetchall())
        return {"items": rows, "total": total, "offset": offset, "limit": limit, "deleted": deleted,
                "occupancy": occupancy, "counts": counts, "storage":self.storage_info()}

    def detail(self, visit_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM visits WHERE id=? AND realm='real'", (visit_id,)).fetchone()
            if not row:
                return None
            visit = dict(row); visit.pop("realm", None); visit.pop("entry_source", None)
            visit["passages"] = [dict(item) for item in conn.execute("SELECT * FROM passages WHERE visit_id=? ORDER BY occurred_at", (visit_id,))]
            visit["corrections"] = [dict(item) for item in conn.execute("SELECT * FROM visit_corrections WHERE visit_id=? ORDER BY confirmed_at", (visit_id,))]
            return visit

    def evidence(self, filename):
        """Chỉ đọc tên ảnh có trong hồ sơ thật, chặn đường dẫn tùy ý."""
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            return None
        with self.connection() as conn:
            row = conn.execute("SELECT 1 FROM passages p JOIN visits v ON v.id=p.visit_id WHERE v.realm='real' AND (p.scene_file=? OR p.crop_file=?)", (filename, filename)).fetchone()
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
