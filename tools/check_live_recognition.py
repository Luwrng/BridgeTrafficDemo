"""Đọc khung camera đã cấu hình qua chính YOLO/Paddle và lưu hồ sơ thật.

Không dùng ảnh mẫu hay tạo sự kiện giả. Link/mật khẩu không đi qua script.
Chỉ chạy theo yêu cầu kiểm tra camera: python tools/check_live_recognition.py.
"""
import argparse
from datetime import datetime, timezone
import json
import time
from urllib.request import Request, urlopen
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera', choices=('A','B','C','E'), default='A')
    parser.add_argument('--frames', type=int, choices=range(1,21), default=6)
    args = parser.parse_args()
    source_key = 'live-check-' + uuid.uuid4().hex
    started = time.monotonic()
    previous = None
    for _ in range(args.frames):
        with urlopen(f'http://127.0.0.1:5083/camera/{args.camera}/status', timeout=3) as response:
            status = json.load(response)
        if not status.get('streamAvailable'):
            print(json.dumps({'camera':args.camera,'streamAvailable':False,'message':status.get('message')}, ensure_ascii=False))
            return
        with urlopen(f'http://127.0.0.1:5083/camera/{args.camera}/frame', timeout=3) as response:
            identifier = response.headers['X-Frame-Id']
            image = response.read()
        if identifier == previous:
            time.sleep(.3)
            continue
        previous = identifier
        received_at = datetime.now(timezone.utc)
        headers = {'Content-Type':'image/jpeg','X-Frame-Id':identifier,'X-Source-Kind':'live',
                   'X-Source-Key':source_key,'X-Camera-Revision':str(status['revision']),
                   'X-Captured-At':received_at.isoformat(),'X-Media-Time':str(time.monotonic()-started),'X-Scene-Cut':'false'}
        request = Request(f'http://127.0.0.1:5084/recognize?cameraId={args.camera}', data=image, headers=headers)
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
        print(json.dumps({'camera':args.camera,'frame':identifier,
                          'plates':[{'plate':item.get('plate'),'confirmed':item.get('confirmed'),'evidence':item.get('evidenceCount')} for item in result.get('detections',[])],
                          'recording':result.get('recording',{}).get('state')}, ensure_ascii=False), flush=True)
        time.sleep(.3)


if __name__ == '__main__':
    main()
