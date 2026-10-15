"""Một nơi chọn database cho cả OCR và API; không tự đổi DB khi mất kết nối."""
import json
import os
from pathlib import Path
from vehicle_records import VisitStore, ROOT, DEFAULT_DATA_DIR

CONFIG_PATH = ROOT / 'Data' / 'database.json'


def read_settings(path=CONFIG_PATH):
    settings = json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else {'backend':'sqlite'}
    # Chỉ dùng override khi chạy kiểm thử/khôi phục có chủ đích.
    if os.environ.get('CAMERA_RECORDS_BACKEND'):
        settings['backend'] = os.environ['CAMERA_RECORDS_BACKEND']
    if settings.get('backend') not in ('sqlite','sqlserver'):
        raise ValueError('Loại database chưa được hỗ trợ.')
    return settings


def create_visit_store(settings=None, data_dir=DEFAULT_DATA_DIR):
    settings = dict(settings) if settings is not None else read_settings()
    if settings['backend'] == 'sqlite': return VisitStore(data_dir)
    if settings['backend'] == 'sqlserver':
        from sqlserver_store import SqlServerVisitStore
        return SqlServerVisitStore(settings, data_dir)
    raise ValueError('Loại database chưa được hỗ trợ.')
