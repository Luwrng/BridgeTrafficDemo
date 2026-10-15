"""Independent loopback camera adapters. Credentials stay in process memory."""
from pathlib import Path
import hashlib
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

CAMERA_IDS = ("A", "B", "C", "E")


def normalize_source(value):
    parts = [part.strip() for part in re.split(r"(?=rtsp://)", str(value).strip(), flags=re.I) if part.strip()]
    if not parts or any(part != parts[0] for part in parts):
        raise ValueError("Chỉ nhập một nguồn RTSP.")
    value = parts[0]
    if len(value) > 4096 or any(character in value for character in "\r\n\x00"):
        raise ValueError("Địa chỉ RTSP không hợp lệ.")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        raise ValueError("Cần URL RTSP của camera trong mạng nội bộ.") from None
    if parsed.scheme.lower() != "rtsp" or not valid_port or address.is_unspecified or not (address.is_private or address.is_loopback):
        raise ValueError("Cần URL RTSP của camera trong mạng nội bộ.")
    return value


def executable_paths():
    vlc = Path(os.environ.get("VLC_EXE", "C:/Program Files/VideoLAN/VLC/vlc.exe"))
    ffmpeg = Path(os.environ["FFMPEG_EXE"]) if os.environ.get("FFMPEG_EXE") else None
    if ffmpeg is None:
        packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
        ffmpeg = next(packages.glob("Gyan.FFmpeg.Shared_Microsoft.Winget.Source_*/*/bin/ffmpeg.exe"), None)
    if not vlc.is_file() or ffmpeg is None or not ffmpeg.is_file():
        raise RuntimeError("Cần VLC và FFmpeg trên máy chủ camera.")
    return str(vlc), str(ffmpeg)


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def jpeg_frames(stream, stopped):
    buffer = bytearray()
    while not stopped.is_set():
        chunk = stream.read1(8192) if hasattr(stream, "read1") else stream.read(8192)
        if not chunk:
            return
        buffer.extend(chunk)
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > 8192:
                    del buffer[:-2]
                break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start:
                    del buffer[:start]
                if len(buffer) > 5_000_000:
                    buffer.clear()
                break
            yield bytes(buffer[start:end + 2])
            del buffer[:end + 2]


class CameraStream:
    def __init__(self, camera_id):
        self.camera_id = camera_id
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.configure_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.processes = []
        self.response = None
        self.url = None
        self.mode = None
        self.revision = 0
        self.frame = None
        self.frame_id = 0
        self.frame_at = 0
        self.message = "Chưa cấu hình nguồn camera."

    def configure(self, mode, url=None):
        if mode not in ("rtsp", "sharedA"):
            raise ValueError("Chọn RTSP riêng hoặc nguồn A dùng chung.")
        if mode == "rtsp":
            url = normalize_source(url)
            executable_paths()  # Validate before disturbing the current source.
        with self.configure_lock:
            self.stop()
            with self.lock:
                self.mode = mode
                self.url = url
                self.revision += 1
                self.stop_event = threading.Event()
                self.message = "Đang kết nối nguồn camera…"
                revision, stopped = self.revision, self.stop_event
            threading.Thread(target=self.run, args=(revision, stopped), daemon=True).start()

    def stop(self):
        with self.lock:
            self.stop_event.set()
            processes = self.processes
            self.processes = []
            self.response = None
            self.url = None
            self.mode = None
            self.frame = None
            self.frame_at = 0
            self.message = "Đã ngắt nguồn camera."
            self.changed.notify_all()
        for process in processes:
            try:
                process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def status(self):
        with self.lock:
            fresh = self.frame is not None and time.monotonic() - self.frame_at < 3
            # Hash nguồn phục vụ đối chiếu; logic vào/ra phân biệt camera_id.
            # Không đưa URL hay tài khoản vào status/DB.
            source_id = None
            if self.url:
                parsed = urlsplit(self.url)
                identity = f"{parsed.hostname}:{parsed.port or 554}{parsed.path}?{parsed.query}"
                source_id = hashlib.sha256(identity.encode()).hexdigest()
            return {"cameraId": self.camera_id, "configured": self.mode is not None,
                    "streamAvailable": fresh, "revision": self.revision, "frameId": self.frame_id,
                    "sharedFrom": "A" if self.mode == "sharedA" else None,
                    "sourceFingerprint": source_id,
                    "sourceName": f"Ô {self.camera_id} · chung nguồn A" if self.mode == "sharedA" else f"RTSP camera {self.camera_id}",
                    "message": "Đang phát hình thật từ nguồn đã chọn." if fresh else self.message}

    def next_frame(self, previous, timeout=2):
        with self.changed:
            self.changed.wait_for(lambda: self.frame_id != previous or self.mode is None, timeout)
            return self.frame_id, self.frame, self.mode is not None

    def publish(self, frame, revision, stopped):
        with self.changed:
            if stopped.is_set() or revision != self.revision:
                return
            self.frame = frame
            self.frame_id += 1
            self.frame_at = time.monotonic()
            self.changed.notify_all()

    def run(self, revision, stopped):
        failures = 0
        while not stopped.is_set():
            processes = []
            try:
                with self.lock:
                    mode, url = self.mode, self.url
                if mode == "sharedA" and self.camera_id != "A":
                    previous = -1
                    while not stopped.is_set():
                        identifier, frame, configured = CAMERAS["A"].next_frame(previous)
                        if frame is not None and identifier != previous:
                            previous = identifier
                            self.publish(frame, revision, stopped)
                        if not configured:
                            stopped.wait(.5)
                elif mode == "sharedA":
                    with urlopen("http://127.0.0.1:5080/api/camera/A/stream", timeout=8) as response:
                        for frame in jpeg_frames(response, stopped):
                            self.publish(frame, revision, stopped)
                elif mode == "rtsp":
                    vlc_path, ffmpeg_path = executable_paths()
                    rc_port, video_port = available_port(), available_port()
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    vlc = subprocess.Popen([vlc_path, "--no-one-instance", "--intf=dummy", "--extraintf=oldrc",
                        f"--rc-host=127.0.0.1:{rc_port}", "--rtsp-tcp", "--no-audio", "--verbose=-1",
                        f"--sout=#standard{{access=http,mux=ts,dst=127.0.0.1:{video_port}/camera}}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                    processes.append(vlc)
                    with self.lock:
                        if stopped.is_set():
                            return
                        self.processes = list(processes)
                    for attempt in range(25):
                        if stopped.is_set():
                            return
                        try:
                            with socket.create_connection(("127.0.0.1", rc_port), timeout=.3) as control:
                                escaped = url.replace("\\", "\\\\").replace('"', '\\"')
                                control.sendall(f'add "{escaped}"\n'.encode("utf-8"))
                            break
                        except OSError:
                            if attempt == 24:
                                raise RuntimeError("Camera converter unavailable") from None
                            stopped.wait(.2)
                    ffmpeg = subprocess.Popen([ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-i",
                        f"http://127.0.0.1:{video_port}/camera", "-an", "-vf", "fps=8,scale=960:-2", "-f",
                        "image2pipe", "-c:v", "mjpeg", "-q:v", "6", "pipe:1"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
                    processes.append(ffmpeg)
                    with self.lock:
                        if stopped.is_set():
                            return
                        self.processes = list(processes)
                    for frame in jpeg_frames(ffmpeg.stdout, stopped):
                        self.publish(frame, revision, stopped)
                else:
                    return
                failures = 0
            except Exception:
                # Never include a URL or decoder stderr in messages/logs.
                with self.lock:
                    if revision == self.revision and not stopped.is_set():
                        self.message = "Chưa nhận được hình. Kiểm tra nguồn camera; đang thử kết nối lại."
                failures += 1
            finally:
                for process in processes:
                    try:
                        process.kill()
                        process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            stopped.wait(min(30, 2 ** min(5, failures + 1)))


CAMERAS = {camera_id: CameraStream(camera_id) for camera_id in CAMERA_IDS}


def close_all():
    for camera in CAMERAS.values():
        camera.stop()
