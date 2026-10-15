"""Tên vật lý tiếng Việt; giữ tên nội bộ/API để không đổi nghiệp vụ.

Chỉ dịch identifier SQL. Giá trị, mã trạng thái, chuỗi literal và tham số
được giữ nguyên hoàn toàn, kể cả khi trùng với tên một bảng hoặc cột.
"""
import re

TABLES = {
    'visits': 'LuotXeRaVao', 'passages': 'LanGhiNhanCamera',
    'gate_state': 'TrangThaiKhuD', 'plate_release': 'ChongGhiTrungBienSo',
    'visit_corrections': 'DieuChinhLuotXe', 'schema_migrations': 'PhienBanCauTruc',
    'migration_runs': 'NhatKyChuyenDuLieu',
}
COLUMNS = {
    'id': 'Ma', 'realm': 'LoaiDuLieu', 'plate': 'BienSo',
    'entered_at': 'ThoiGianVao', 'exited_at': 'ThoiGianRa',
    'entry_camera': 'CongVao', 'exit_camera': 'CongRa', 'status': 'TrangThai',
    'issue': 'GhiChu', 'created_at': 'ThoiGianTao', 'entry_source': 'MaNguonVao',
    'deleted_at': 'ThoiGianXoa', 'visit_id': 'MaLuotXe', 'event_key': 'MaSuKien',
    'direction': 'LoaiGhiNhan', 'camera_id': 'MaCamera', 'occurred_at': 'ThoiGianGhiNhan',
    'source_kind': 'LoaiNguon', 'source_name': 'TenNguon', 'media_time': 'ThoiDiemVideo',
    'confidence': 'DoTinCay', 'scene_file': 'TepAnhToanCanh', 'crop_file': 'TepAnhBienSo',
    'scene_sha256': 'MaKiemTraAnhToanCanh', 'crop_sha256': 'MaKiemTraAnhBienSo',
    'revision': 'PhienBanTrangThai', 'last_seen_at': 'ThoiGianThayCuoi',
    'action': 'ThaoTac', 'confirmed_at': 'ThoiGianXacNhan', 'reason': 'LyDo',
    'version': 'PhienBan', 'installed_at': 'ThoiGianCaiDat',
    'source_sha256': 'MaKiemTraNguon', 'backup_path': 'DuongDanSaoLuu',
    'counts_json': 'ThongKeSoDong', 'completed_at': 'ThoiGianHoanThanh',
}
_REVERSE_COLUMNS = {value.lower(): key for key, value in COLUMNS.items()}
_IDENTIFIERS = {**TABLES, **COLUMNS}
_TOKEN = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/|N?'(?:''|[^'])*'|\[[^\]]+\]|[A-Za-z_][A-Za-z_0-9]*|\?", re.I)


def logical_column(name):
    return _REVERSE_COLUMNS.get(name.lower(), name)


def translate_sql(sql, parameters=()):
    """Chuyển tên trong các câu SQL của repository; không thay dữ liệu.

    Quét theo token để tránh sửa chữ trong ghi chú hoặc literal. Không dùng
    hàm này cho SQL tùy ý do người dùng cung cấp.
    """
    def replace(match):
        token = match[0]
        if token == '?' or token.startswith(('--', '/*', "'")) or token[:2].upper() == "N'":
            return token
        mapped = _IDENTIFIERS.get(token.strip('[]').lower())
        return '[' + mapped + ']' if mapped else token

    translated = _TOKEN.sub(replace, sql)
    return translated, tuple(parameters)
