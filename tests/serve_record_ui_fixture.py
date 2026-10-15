"""Kiểm tra UI có dữ liệu trong DB TẠM; không phục vụ camera hay ghi DB ứng dụng.

Chỉ dùng lúc kiểm thử: python tests/serve_record_ui_fixture.py. Trang tại
5086 có nhãn KIỂM THỬ CÁCH LY. Các sự kiện bên dưới là fixture của quy tắc
nghiệp vụ, không phải lượt xe thực tế. Ảnh fixture là ảnh RTSP đã chụp.
"""
from datetime import datetime, timedelta, timezone
import mimetypes
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from vehicle_records import VisitStore
from vehicle_record_server import RecordHTTPServer, RecordHandler, ALLOWED_ORIGINS


class FixtureHandler(RecordHandler):
    allowed_origins = ALLOWED_ORIGINS | {"http://127.0.0.1:5086"}
    def do_GET(self):
        route = urlsplit(self.path).path
        if route.startswith(("/api/", "/evidence/")) or route == "/status":
            return super().do_GET()
        static_root = (ROOT / "wwwroot").resolve()
        path = (static_root / (route.lstrip("/") or "index.html")).resolve()
        if not path.is_relative_to(static_root) or not path.is_file():
            return self.respond(404, {"error": "Fixture path not found"})
        data = path.read_bytes()
        if path.suffix in (".js", ".html"):
            text = data.decode("utf-8")
            if path.name == "recognition.js":
                # Chặn toàn bộ camera/OCR trong fixture để không ảnh hưởng nguồn
                # hoặc ghi lượt thật nếu người dùng đang cấu hình camera khác.
                text = "window.CameraRecognition={init(){},hasVisualSource(){return false},recordingAvailable(){return true}};"
            # Hai module chỉ đọc DB tạm cùng origin. Mã nguồn ứng dụng không đổi.
            text = text.replace("http://127.0.0.1:5085", "")
            if path.suffix == ".html":
                text = text.replace("<h1>Camera giám sát</h1>", "<h1>KIỂM THỬ CÁCH LY</h1>")
                text = text.replace("Theo dõi camera · Sơ đồ vùng D", "Sự kiện kiểm thử · DB tạm · không ghi hồ sơ ứng dụng")
            data = text.encode("utf-8")
        self.send_response(200)
        self.headers_for(mimetypes.guess_type(path.name)[0] or "application/octet-stream", len(data))
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/qa/shutdown":
            self.respond(200, {"stopped": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path != "/qa/exit":
            return super().do_POST()
        # Đường này chỉ tồn tại trong fixture, không có trong server ứng dụng.
        self.respond(200, self.store.record(fixture_event("fixture-exit", "C", "51K73109", 1)))


def fixture_event(key, camera, plate, seconds):
    return {"event_key": key, "plate": plate, "camera_id": camera,
            "occurred_at": (START + timedelta(seconds=seconds)).isoformat(),
            "source_kind": "live", "confidence": .9, "media_time": max(0, seconds + 120),
            "scene": (ROOT / "artifacts/ui-check/camera-live.jpg").read_bytes(),
            "crop": (ROOT / "artifacts/ui-check/plate-crop.jpg").read_bytes()}


if __name__ == "__main__":
    START = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="isolated-vehicle-ui-") as directory:
        FixtureHandler.store = VisitStore(directory)
        FixtureHandler.store.record(fixture_event("fixture-entry", "A", "51K73109", -120))
        FixtureHandler.store.record(fixture_event("fixture-wait", "B", "51F00651", -60))
        with RecordHTTPServer(("127.0.0.1", 5086), FixtureHandler) as server:
            print("ISOLATED UI FIXTURE on 5086; temporary database only", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
