"""API hồ sơ cục bộ 5085; tách khỏi luồng RTSP để cập nhật không mất link camera.

Database do record_storage chọn chung với OCR. API có xóa/khôi phục danh
sách và xác nhận ra có audit; không trực tiếp điều khiển đèn/PLC.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import socket
from urllib.parse import parse_qs, urlsplit
from vehicle_records import VisitStore
from record_storage import create_visit_store

ALLOWED_ORIGINS = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5000, 5080, 5082)}


class RecordHTTPServer(ThreadingHTTPServer):
    """Một dịch vụ sở hữu cổng: Windows không được nhận nhầm bản chạy cũ.

    SO_REUSEADDR của HTTPServer trên Windows có thể cho nhiều tiến trình
    cùng bind một địa chỉ. Dùng exclusive bind để lần chạy thứ hai báo lỗi
    rõ ràng thay vì trả phản hồi lẫn lộn giữa hai phiên bản.
    """
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class RecordHandler(BaseHTTPRequestHandler):
    store = None
    allowed_origins = ALLOWED_ORIGINS

    def log_message(self, *_):
        """Không in biển số, ảnh hoặc thông tin đăng nhập vào log máy chủ."""

    def headers_for(self, content_type, length):
        """CORS chỉ cho ứng dụng local, dùng chung cho JSON và ảnh bằng chứng."""
        origin = self.headers.get("Origin")
        if origin in self.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def respond(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.headers_for("application/json; charset=utf-8", len(encoded))
        try:
            self.wfile.write(encoded)
        except (ConnectionError, OSError):
            pass

    def do_OPTIONS(self):
        self.respond(200, {})

    def do_GET(self):
        """Đọc hồ sơ thật/ảnh gốc; không còn bộ chọn loại dữ liệu hay hướng."""
        route = urlsplit(self.path)
        params = parse_qs(route.query)
        try:
            if params.get("realm", ["real"])[0] != "real":
                self.respond(400, {"error": "Chỉ phục vụ hồ sơ camera thật."}); return
            if route.path == "/status":
                self.respond(200, {"available": True, "schemaVersion": 6, "serviceVersion": "3.1", "storage":self.store.storage_info()}); return
            if route.path == "/api/occupancy":
                self.respond(200, self.store.occupancy()); return
            if route.path == "/api/visits":
                deleted = params.get("deleted", ["0"])[0]
                if deleted not in ("0", "1"):
                    raise ValueError("Bộ lọc hồ sơ không hợp lệ")
                self.respond(200, self.store.list_visits(int(params.get("offset", ["0"])[0]), int(params.get("limit", ["30"])[0]), deleted == "1")); return
            visit = re.fullmatch(r"/api/visits/([0-9a-f]{32})", route.path)
            if visit:
                detail = self.store.detail(visit[1])
                self.respond(200 if detail else 404, detail or {"error": "Không tìm thấy hồ sơ."}); return
            image = re.fullmatch(r"/evidence/([0-9a-f]{32}_(?:scene|plate)\.jpg)", route.path)
            if image:
                file = self.store.evidence(image[1])
                if not file:
                    self.respond(404, {"error": "Không tìm thấy ảnh bằng chứng."}); return
                data = file.read_bytes()
                self.send_response(200)
                self.headers_for("image/jpeg", len(data))
                self.wfile.write(data)
                return
            self.respond(404, {"error": "Đường dẫn không tồn tại."})
        except (ValueError, TypeError):
            self.respond(400, {"error": "Tham số truy vấn không hợp lệ."})
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception:
            self.respond(500, {"error": "Không đọc được hồ sơ hoặc bằng chứng trên máy."})

    def do_POST(self):
        """Chỉ xóa có khôi phục, không cho tạo/sửa sự kiện xe vào–ra."""
        if self.headers.get("Origin") not in self.allowed_origins | {None}:
            self.respond(403, {"error": "Chỉ cho phép ứng dụng local."}); return
        action = re.fullmatch(r"/api/visits/([0-9a-f]{32})/(trash|restore|confirm-exit)", urlsplit(self.path).path)
        if not action:
            self.respond(405, {"error": "Hồ sơ vào–ra chỉ được ghi từ nhận diện camera."}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096 or not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("Cần xác nhận JSON hợp lệ.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or payload.get("confirm") is not True:
                raise ValueError("Chưa xác nhận thao tác hồ sơ.")
            result = self.store.confirm_exit(action[1], payload.get('camera'), payload.get('occurredAt'), payload.get('reason', '')) if action[2] == 'confirm-exit' else self.store.set_deleted(action[1], action[2] == "trash")
            self.respond(200 if result else 404, result or {"error": "Không tìm thấy hồ sơ."})
        except (ValueError, TypeError) as error:
            self.respond(400, {"error": str(error) if action[2] == 'confirm-exit' else "Yêu cầu xóa/khôi phục không hợp lệ."})
        except Exception:
            self.respond(500, {"error": "Không cập nhật được hồ sơ trên ổ lưu."})


if __name__ == "__main__":
    RecordHandler.store = create_visit_store()
    server = RecordHTTPServer(("127.0.0.1", 5085), RecordHandler)
    server.daemon_threads = True
    print("Vehicle records v3.1 ready on 127.0.0.1:5085", flush=True)
    server.serve_forever()
