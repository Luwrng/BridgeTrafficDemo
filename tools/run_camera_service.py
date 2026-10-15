"""Chạy một dịch vụ nền và lưu log khởi động; không lưu thông tin RTSP.

Launcher Windows tạo process độc lập với terminal. Wrapper chỉ chọn ba
script cố định của dự án, không thực thi đường dẫn hay lệnh từ giao diện.
"""
import argparse
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    "camera": ("local_ocr.py", []),
    "ocr": ("local_ocr.py", ["--port", "5084", "--ocr-only"]),
    "records": ("vehicle_record_server.py", []),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=SERVICES, required=True)
    args = parser.parse_args()
    log_dir = ROOT / "Data" / "service-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{args.service}.log"
    script, options = SERVICES[args.service]
    with log.open("a", encoding="utf-8", buffering=1) as output:
        sys.stdout = output; sys.stderr = output
        sys.path.insert(0, str(ROOT / "tools"))
        sys.argv = [str(ROOT / "tools" / script), *options]
        print(f"Starting {args.service} service", flush=True)
        try:
            runpy.run_path(sys.argv[0], run_name="__main__")
        except Exception:
            # Startup/model/storage failures are visible in the local log.
            # API handlers and camera adapters already exclude credentials.
            traceback.print_exc()
            raise SystemExit(1)


if __name__ == "__main__":
    main()
