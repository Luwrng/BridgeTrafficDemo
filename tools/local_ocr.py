"""Local YOLO/PaddleOCR companion; live camera workers can remain running separately."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import io
import base64
import json
import re
import sys
import threading
import uuid
from urllib.parse import unquote, urlsplit, parse_qs
from plate_tracking import PlateTracker, iou
from camera_streams import CAMERAS, CAMERA_IDS, close_all
from vehicle_records import VisitStore, PassageRecorder, utc_time, GATE_LOGIC_VERSION
from record_storage import create_visit_store
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "artifacts" / "ocr-python-deps"))
import numpy as np
import onnxruntime as ort
from PIL import Image
from paddle_plate_ocr import PaddlePlateOCR

OPTIONS = ort.SessionOptions()
OPTIONS.intra_op_num_threads = 2
OPTIONS.log_severity_level = 3
SESSION = ort.InferenceSession(str(ROOT / "Models" / "license-plate-yolov8n.onnx"),
                              sess_options=OPTIONS, providers=["CPUExecutionProvider"])
PLATE_OCR = PaddlePlateOCR()
PIPELINE_VERSION = 2
ENGINE_SESSION = uuid.uuid4().hex
VISIT_STORE = create_visit_store()
PASSAGE_RECORDER = PassageRecorder(VISIT_STORE)
GATE = threading.Lock()
TRACKERS = {}
ALLOWED_ORIGINS = {"http://127.0.0.1:5080", "http://localhost:5080",
                   "http://127.0.0.1:5000", "http://localhost:5000",
                   "http://127.0.0.1:5082", "http://localhost:5082"}


def record_camera_passage(headers, camera_id, source_key, detections, frame_data, width, height, media_time, frame_id, received_at=None):
    """Nối OCR vào hồ sơ mà không điều khiển đèn hoặc đổi nguồn camera.

    Không cần chọn hướng. Chỉ RTSP riêng, khung mới và đúng revision mới
    được ghi hồ sơ; video mở từ file chỉ xem/nhận diện, không ghi lượt vào–ra.
    Lỗi lưu hồ sơ được trả riêng, không làm mất kết quả OCR/luồng hình đang chạy.
    """
    try:
        kind = headers.get("X-Source-Kind", "")
        if kind != "live":
            return {"state": "preview-only", "visitIds": []}
        real_source = False
        if kind == "live":
            with urlopen(f"http://127.0.0.1:5083/camera/{camera_id}/status", timeout=.8) as response:
                status = json.load(response)
            # Không gắn khung của nguồn cũ vào nguồn vừa được người dùng đổi.
            if not status.get("configured") or not status.get("streamAvailable") or str(status.get("revision")) != headers.get("X-Camera-Revision"):
                return {"state": "source-changed", "visitIds": []}
            if not frame_id.isdecimal() or not 0 < int(frame_id) <= status.get("frameId", 0):
                return {"state": "stale-frame", "visitIds": []}
            real_source = not status.get("sharedFrom")
            if not real_source:
                return {"state": "preview-only", "visitIds": []}
        captured_at = utc_time(headers.get("X-Captured-At") or datetime.now(timezone.utc).isoformat())
        # Kiểm tra độ mới lúc server nhận ảnh, không phải sau suy luận OCR.
        # CPU chậm không được làm mất lượt đã nhận từ một khung còn mới.
        age = ((received_at or datetime.now(timezone.utc)) - datetime.fromisoformat(captured_at)).total_seconds()
        if age < -5 or age > 10:
            return {"state": "stale-frame", "visitIds": []}
        # Frontend gửi JPEG thật; vẫn chuyển đúng nội dung nếu client hợp lệ gửi PNG.
        scene = frame_data
        if not scene.startswith(b"\xff\xd8") or not scene.endswith(b"\xff\xd9"):
            encoded = io.BytesIO()
            with Image.open(io.BytesIO(frame_data)) as frame:
                frame.convert("RGB").save(encoded, format="JPEG", quality=92)
            scene = encoded.getvalue()
        return PASSAGE_RECORDER.observe(camera_id=camera_id, source_key=source_key,
            engine_session=ENGINE_SESSION, detections=detections, frame_id=frame_id,
            media_time=media_time, captured_at=captured_at, width=width, height=height,
            scene=scene, source_kind=kind, source_name=unquote(headers.get("X-Source-Name", f"Camera {camera_id}"))[:200],
            real_source=real_source, scene_cut=headers.get("X-Scene-Cut") == "true",
            source_fingerprint=status.get("sourceFingerprint"))
    except Exception:
        return {"state": "error", "message": "Không ghi được hồ sơ/bằng chứng; kiểm tra dịch vụ và ổ lưu dữ liệu.", "visitIds": []}


def detect_once(image):
    width, height = image.size
    scale = min(640 / width, 640 / height)
    scaled = image.resize((round(width * scale), round(height * scale)), Image.Resampling.BILINEAR)
    frame = Image.new("RGB", (640, 640), (114, 114, 114))
    pad_x, pad_y = (640 - scaled.width) // 2, (640 - scaled.height) // 2
    frame.paste(scaled, (pad_x, pad_y))
    tensor = np.asarray(frame, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    output = SESSION.run(None, {SESSION.get_inputs()[0].name: tensor})[0][0]
    if output.shape[0] < output.shape[1]:
        output = output.T
    candidates = []
    for row in output[output[:, 4] > .20]:
        cx, cy, box_width, box_height = map(float, row[:4])
        if max(cx, cy) <= 1.5:
            cx *= 640; cy *= 640; box_width *= 640; box_height *= 640
        x1 = max(0, (cx - box_width / 2 - pad_x) / scale)
        y1 = max(0, (cy - box_height / 2 - pad_y) / scale)
        x2 = min(width, (cx + box_width / 2 - pad_x) / scale)
        y2 = min(height, (cy + box_height / 2 - pad_y) / scale)
        if x2 - x1 >= 20 and y2 - y1 >= 10 and 1 <= (x2 - x1) / (y2 - y1) <= 6.5:
            candidates.append({"box": [x1, y1, x2 - x1, y2 - y1], "detectorConfidence": float(row[4])})
    kept = []
    for candidate in sorted(candidates, key=lambda item: item["detectorConfidence"], reverse=True):
        if all(iou(candidate["box"], prior["box"]) < .45 for prior in kept):
            kept.append(candidate)
    return kept


def detect_plates(image, search_boxes=()):
    candidates = detect_once(image)
    if candidates:
        return candidates
    width, height = image.size
    # Revisit a region found in earlier real frames before scanning the scene.
    for x, y, w, h in search_boxes:
        cx, cy = x + w / 2, y + h / 2
        half = max(100, w * 2, h * 2)
        left, top = max(0, int(cx - half)), max(0, int(cy - half))
        right, bottom = min(width, int(cx + half)), min(height, int(cy + half))
        for item in detect_once(image.crop((left, top, right, bottom))):
            bx, by, bw, bh = item["box"]
            candidates.append({**item, "box": [bx + left, by + top, bw, bh]})
    if candidates:
        return candidates
    # Small plates disappear when a whole scene is resized to the detector's 640px input.
    tile_width, tile_height = round(width * .4), round(height * .55)
    for left in (0, round(width * .3), width - tile_width):
        for top in (0, height - tile_height):
            for item in detect_once(image.crop((left, top, left + tile_width, top + tile_height))):
                bx, by, bw, bh = item["box"]
                if bx < 2 or by < 2 or bx + bw > tile_width - 2 or by + bh > tile_height - 2:
                    continue  # Don't treat a plate cut by a tile boundary as a whole plate.
                candidate = {**item, "box": [bx + left, by + top, bw, bh]}
                if all(iou(candidate["box"], prior["box"]) < .45 for prior in candidates):
                    candidates.append(candidate)
    return candidates


def read_plate(image):
    return PLATE_OCR.read(image)


def recognize(data, search_boxes=()):
    with Image.open(io.BytesIO(data)) as source:
        if not 1 <= source.width <= 4096 or not 1 <= source.height <= 4096:
            raise ValueError("Invalid frame dimensions")
        image = source.convert("RGB")
    detections = []
    for detection in detect_plates(image, search_boxes):
        x, y, box_width, box_height = detection["box"]
        dx = max(4, box_width * .15); dy = max(4, box_height * .25)
        bounds = (max(0, int(x - dx)), max(0, int(y - dy)),
                  min(image.width, int(x + box_width + dx)), min(image.height, int(y + box_height + dy)))
        crop = image.crop(bounds)
        context_bounds = (max(0, int(x - box_width)), max(0, int(y - box_height)),
                          min(image.width, int(x + box_width * 2)), min(image.height, int(y + box_height * 2)))
        context = np.asarray(image.crop(context_bounds).resize((32, 24), Image.Resampling.BILINEAR))
        appearance = []
        for channel in range(3):
            histogram = np.histogram(context[:, :, channel], bins=8, range=(0, 256))[0]
            appearance.extend((histogram / histogram.sum()).tolist())
        thumbnail = crop.copy(); thumbnail.thumbnail((320, 180))
        preview = io.BytesIO(); thumbnail.save(preview, format="JPEG", quality=85)
        plate, raw, confidence = read_plate(crop)
        detections.append({**detection, "plate": plate, "rawText": raw, "confidence": confidence, "appearance": appearance,
                           "crop": "data:image/jpeg;base64," + base64.b64encode(preview.getvalue()).decode("ascii")})
    return detections, image.width, image.height


class Handler(BaseHTTPRequestHandler):
    ocr_only = False
    def handle(self):
        try:
            super().handle()
        except (ConnectionError, OSError):
            pass  # A canceled frame request is a normal browser/source transition.

    def log_message(self, *_):
        pass  # Never log frames, plate text, or credentials.

    def respond(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Frame-Width, X-Frame-Height, X-Source-Key, X-Source-Name, X-Frame-Id, X-Media-Time, X-Scene-Cut, X-Source-Kind, X-Captured-At, X-Camera-Revision")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (ConnectionError, OSError):
            pass

    def do_OPTIONS(self):
        self.respond(200, {})

    def do_GET(self):
        camera_match = re.fullmatch(r"/camera/([ABCE])/(status|stream|frame)", urlsplit(self.path).path)
        if camera_match:
            if self.ocr_only:
                self.respond(404, {"error": "Recognition service only"}); return
            camera_id, action = camera_match.groups()
            camera = CAMERAS[camera_id]
            if action == "status":
                self.respond(200, camera.status()); return
            if not camera.status()["configured"]:
                self.respond(503, {"error": "Camera chưa có nguồn."}); return
            if action == "frame":
                identifier, frame, _ = camera.next_frame(-1, timeout=0)
                if frame is None or not camera.status()["streamAvailable"]:
                    self.respond(503, {"error": "Chưa nhận được khung hình mới."}); return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Id", str(identifier))
                self.send_header("Access-Control-Expose-Headers", "X-Frame-Id")
                origin = self.headers.get("Origin")
                if origin in ALLOWED_ORIGINS:
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                self.wfile.write(frame)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()
            try:
                previous = -1
                while True:
                    identifier, frame, configured = camera.next_frame(previous)
                    if not configured:
                        return
                    if frame is None or identifier == previous:
                        continue
                    previous = identifier
                    self.wfile.write(f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame + b"\r\n")
                    self.wfile.flush()
            except (ConnectionError, OSError):
                pass
            return
        if self.path == "/status":
            self.respond(200, {"available": True, "engine": "YOLOv8n/PaddleOCR PP-OCRv4 (ONNX)", "pipelineVersion": PIPELINE_VERSION, "cameraIds": list(CAMERA_IDS), "recordingAvailable": True, "recordingVersion": 2, "gateLogicVersion": GATE_LOGIC_VERSION, "storage":VISIT_STORE.storage_info()})
        else:
            self.respond(404, {"error": "Unknown endpoint"})

    def do_POST(self):
        if self.headers.get("Origin") not in ALLOWED_ORIGINS | {None}:
            self.respond(403, {"error": "Local demo origin required"}); return
        parsed_path = urlsplit(self.path)
        camera_match = re.fullmatch(r"/camera/([ABCE])/configure", parsed_path.path)
        if camera_match:
            if self.ocr_only:
                self.respond(404, {"error": "Recognition service only"}); return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 8192:
                self.respond(400, {"error": "Cấu hình nguồn không hợp lệ."}); return
            try:
                payload = json.loads(self.rfile.read(length))
                camera = CAMERAS[camera_match[1]]
                if payload.get("mode") == "disconnect":
                    camera.stop()
                else:
                    camera.configure(payload.get("mode", "rtsp"), payload.get("rtspUrl"))
                self.respond(200, camera.status())
            except (ValueError, RuntimeError) as error:
                self.respond(400, {"error": str(error)})
            except Exception:
                self.respond(500, {"error": "Không cấu hình được nguồn camera."})
            return
        if parsed_path.path != "/recognize":
            self.respond(404, {"error": "Unknown endpoint"}); return
        camera_id = parse_qs(parsed_path.query).get("cameraId", ["A"])[0]
        if camera_id not in CAMERA_IDS:
            self.respond(400, {"error": "Camera không hợp lệ."}); return
        length = int(self.headers.get("Content-Length", "0"))
        if not 1 <= length <= 2_000_000 or not self.headers.get("Content-Type", "").startswith("image/"):
            self.respond(400, {"error": "Need a camera image up to 2 MB"}); return
        received_at = datetime.now(timezone.utc)
        if not GATE.acquire(timeout=4):
            self.respond(429, {"error": "OCR đang xử lý khung hình trước."}); return
        try:
            frame_data = self.rfile.read(length)
            source_key = self.headers.get("X-Source-Key", "legacy")[:200]
            tracker_key = (camera_id, source_key)
            frame_id = self.headers.get("X-Frame-Id", "")[:100]
            frame_time = float(self.headers.get("X-Media-Time", "0"))
            if not np.isfinite(frame_time) or frame_time < 0:
                raise ValueError("Invalid frame timestamp")
            if tracker_key not in TRACKERS:
                if len(TRACKERS) >= 32:
                    del TRACKERS[next(iter(TRACKERS))]
                TRACKERS[tracker_key] = PlateTracker()
            tracker = TRACKERS[tracker_key]
            search_boxes = [track.box for track in tracker.tracks.values()] if self.headers.get("X-Scene-Cut") != "true" else []
            observations, width, height = recognize(frame_data, search_boxes)
            detections = tracker.update(observations, frame_time, frame_id,
                scene_cut=self.headers.get("X-Scene-Cut") == "true")
            recording = record_camera_passage(self.headers, camera_id, source_key, detections, frame_data, width, height, frame_time, frame_id, received_at)
            readable = next((item for item in detections if item["plate"]), None)
            self.respond(200, {"cameraId": camera_id, "plate": readable["plate"] if readable else None,
                "rawText": readable["rawText"] if readable else "", "confidence": readable["confidence"] if readable else 0,
                "message": "Đã xử lý khung hình thực tế." if detections else "Không thấy biển số trong khung hình.",
                "detectedAt": datetime.now(timezone.utc).isoformat(), "detections": detections,
                "frameWidth": width, "frameHeight": height, "sourceKey": source_key,
                "sourceName": unquote(self.headers.get("X-Source-Name", "Camera A")),
                "frameId": frame_id, "videoTime": frame_time, "engineSession": ENGINE_SESSION, "recording": recording})
        except Exception:
            self.respond(500, {"error": "Không xử lý được khung hình bằng OCR cục bộ."})
        finally:
            GATE.release()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, choices=(5083, 5084), default=5083)
    parser.add_argument("--ocr-only", action="store_true")
    args = parser.parse_args()
    Handler.ocr_only = args.ocr_only
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"Local YOLO/PaddleOCR ready on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        close_all()
