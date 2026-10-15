"""SQL Server adapter dùng lại VisitStore và toàn bộ quy tắc vào/ra.

Không có logic OCR hoặc đèn trong adapter. Khóa ứng dụng của SQL Server
serialize các quyết định từ nhiều process; transaction đọc dùng Shared lock
để danh sách/revision/D thuộc cùng snapshot. Không fallback sang SQLite khi lỗi.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import socket
import struct
import sys

from vehicle_records import VisitStore, ROOT, DEFAULT_DATA_DIR
from sqlserver_vietnamese import translate_sql, logical_column

sys.path.insert(0, str(ROOT / 'artifacts' / 'sqlserver-python-deps'))


def driver():
    try:
        import pyodbc
    except ImportError:
        raise RuntimeError('Thiếu pyodbc. Cài tools/requirements-sqlserver.txt vào artifacts/sqlserver-python-deps.') from None
    return pyodbc


def connection_string(settings, database=None):
    """Windows Authentication: không tạo/lưu tài khoản SQL hoặc mật khẩu.

    TrustServerCertificate chỉ cho instance trên chính máy này theo cấu hình
    local đã chọn. Máy chủ từ xa phải dùng chứng chỉ được xác minh.
    """
    server = settings['server']
    database = database or settings['database']
    local_names = {'.', '(local)', 'localhost', '127.0.0.1', socket.gethostname().lower()}
    if settings.get('trustServerCertificate', False) and server.split('\\')[0].lower() not in local_names:
        raise ValueError('Chỉ cho TrustServerCertificate với SQL Server trên máy cục bộ.')
    if not re.fullmatch(r'[A-Za-z0-9_]+', database):
        raise ValueError('Tên database chỉ gồm chữ, số và dấu gạch dưới.')
    def quote(value):
        return '{' + str(value).replace('}', '}}') + '}'
    return ';'.join((f"DRIVER={quote(settings.get('driver', 'ODBC Driver 18 for SQL Server'))}",
                     f'SERVER={quote(server)}', f'DATABASE={quote(database)}',
                     'Trusted_Connection=Yes', 'Encrypt=Yes',
                     'TrustServerCertificate=' + ('Yes' if settings.get('trustServerCertificate', False) else 'No'))) + ';'


def decode_datetimeoffset(value):
    # SQL_SS_TIMESTAMPOFFSET (ODBC -155), nanosecond fraction -> microseconds.
    year, month, day, hour, minute, second, fraction, offset_hour, offset_minute = struct.unpack('<6hI2h', value)
    return datetime(year,month,day,hour,minute,second,fraction//1000,
                    tzinfo=timezone(timedelta(hours=offset_hour, minutes=offset_minute)))


def open_connection(settings, database=None):
    raw = driver().connect(connection_string(settings, database), timeout=8, autocommit=True)
    raw.timeout = 15
    raw.add_output_converter(-155, decode_datetimeoffset)
    return raw


class SqlRow:
    """Hành vi như sqlite3.Row: dict(row), row['name'], row[0], tuple(row)."""
    def __init__(self, columns, values):
        self.columns = columns
        self.values = tuple(value.astimezone(timezone.utc).isoformat(timespec='milliseconds')
                            if isinstance(value, datetime) else value for value in values)
    def keys(self): return self.columns
    def __getitem__(self, key): return self.values[key if isinstance(key,int) else self.columns.index(key)]
    def __iter__(self): return iter(self.values)


class SqlCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = [logical_column(item[0]) for item in cursor.description] if cursor.description else []
    def fetchone(self):
        row = self.cursor.fetchone()
        return SqlRow(self.columns,row) if row is not None else None
    def fetchall(self): return [SqlRow(self.columns,row) for row in self.cursor.fetchall()]
    def __iter__(self): return iter(self.fetchall())


class SqlConnection:
    def __init__(self, raw): self.raw = raw; self.started = False
    def begin(self, exclusive):
        if self.started: raise RuntimeError('Transaction đã được mở.')
        self.raw.execute('SET XACT_ABORT ON; SET NOCOUNT ON; BEGIN TRANSACTION;')
        self.started = True
        # LockOwner=Transaction tự nhả khóa sau COMMIT/ROLLBACK hoặc mất kết nối.
        self.raw.execute("""DECLARE @rc int;
            EXEC @rc=sys.sp_getapplock @Resource=N'CameraVehicleGate', @LockMode=?,
                @LockOwner='Transaction', @LockTimeout=10000;
            IF @rc < 0 THROW 51001, 'Khong khoa duoc trang thai xe; thu lai.', 1;""",
                         'Exclusive' if exclusive else 'Shared')

    def execute(self, sql, parameters=()):
        normalized = sql.strip().upper()
        if normalized in ('BEGIN', 'BEGIN IMMEDIATE'):
            self.begin(normalized == 'BEGIN IMMEDIATE')
            return None
        if not self.started: self.begin(True)
        parameters = tuple(parameters)
        # Chỉ chuyển những cú pháp SQLite đang có trong repository; mọi giá
        # trị người dùng/OCR vẫn là tham số, không nối dữ liệu vào SQL.
        if normalized.startswith('UPDATE PLATE_RELEASE SET LAST_SEEN_AT=MAX('):
            at, plate = parameters
            sql = 'UPDATE plate_release SET last_seen_at=CASE WHEN last_seen_at>? THEN last_seen_at ELSE ? END WHERE plate=?'
            parameters = (at,at,plate)
        elif normalized.startswith('INSERT OR REPLACE INTO PLATE_RELEASE'):
            plate, at = parameters
            sql = 'UPDATE plate_release SET last_seen_at=? WHERE plate=?; IF @@ROWCOUNT=0 INSERT INTO plate_release(plate,last_seen_at) VALUES (?,?)'
            parameters = (at,plate,plate,at)
        elif re.search(r'\sLIMIT\s+\?\s+OFFSET\s+\?\s*$',sql,re.I):
            sql = re.sub(r'\sLIMIT\s+\?\s+OFFSET\s+\?\s*$', ' OFFSET ? ROWS FETCH NEXT ? ROWS ONLY', sql, flags=re.I)
            parameters = (*parameters[:-2],parameters[-1],parameters[-2])
        elif re.search(r'\sLIMIT\s+1\s*$',sql,re.I):
            sql = re.sub(r'\sLIMIT\s+1\s*$', '', sql, flags=re.I)
            sql = re.sub(r'^\s*SELECT\s+', 'SELECT TOP (1) ', sql, count=1, flags=re.I)
        sql, parameters = translate_sql(sql, parameters)
        return SqlCursor(self.raw.execute(sql, parameters))

    def finish(self, failed):
        if self.started:
            self.raw.execute('IF @@TRANCOUNT>0 ' + ('ROLLBACK' if failed else 'COMMIT') + ' TRANSACTION')
            self.started = False


class SqlServerVisitStore(VisitStore):
    def __init__(self, settings, data_dir=DEFAULT_DATA_DIR, verify=True):
        self.settings = dict(settings)
        self.root = Path(data_dir).resolve()
        self.images = self.root / 'evidence'
        self.images.mkdir(parents=True,exist_ok=True)
        self.database = settings['database']
        # Schema chỉ được tạo/nâng cấp bởi lệnh setup; runtime không tự sửa DB.
        if verify:
            with self.connection() as conn:
                conn.execute('BEGIN')
                if conn.execute('SELECT MAX(version) FROM dbo.schema_migrations').fetchone()[0] != 7:
                    raise RuntimeError('Database cần nâng cấp cấu trúc hồ sơ 7 bằng tools/upgrade_records_v7.py.')

    @contextmanager
    def connection(self):
        raw = open_connection(self.settings)
        conn = SqlConnection(raw)
        try:
            yield conn
            conn.finish(False)
        except BaseException:
            conn.finish(True)
            raise
        finally: raw.close()

    def storage_info(self):
        return {'backend':'sqlserver','server':self.settings['server'],'database':self.database}
