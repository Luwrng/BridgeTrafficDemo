# Quản lý xe ra vào — ASP.NET Core + HTML + Python

Giám sát bốn nguồn RTSP riêng A/B/C/E, đọc biển số bằng YOLO → PaddleOCR,
lưu hồ sơ/ngày giờ/ảnh bằng chứng và hiển thị trạng thái vùng D trên sơ đồ.

Máy hiện tại đã cấu hình **SQL Server `.\SQLEXPRESS`, database `QuanLyXe`,
Windows Authentication**. Xem [phân tích backend và các bảng](docs/BACKEND_SQL_SERVER.md).

## Chuẩn bị sau khi clone trên máy mới

Cần .NET 8 SDK, Python 3.12, VLC và FFmpeg. Nếu dùng SQL Server, cài thêm
SQL Server Express và Microsoft ODBC Driver 18. Chạy ở thư mục gốc project:

```powershell
$env:CAMERA_PYTHON = (Get-Command python).Source
python -m pip install --target artifacts/ocr-python-deps -r tools/requirements-ocr.txt
python -m pip install --target artifacts/sqlserver-python-deps -r tools/requirements-sqlserver.txt
dotnet publish BridgeTrafficDemo.csproj -c Release -o artifacts/current-server
```

Nếu FFmpeg không nằm trong thư mục WinGet được nhận diện tự động, đặt
`FFMPEG_EXE` thành đường dẫn đầy đủ tới `ffmpeg.exe`. Tương tự, đặt `VLC_EXE`
nếu VLC được cài ngoài `C:/Program Files/VideoLAN/VLC/vlc.exe`.

Để chuẩn bị **database mới trên máy mới**, dùng Windows Authentication:

```powershell
python tools/setup_sqlserver.py --server .\SQLEXPRESS --database QuanLyXe --trust-local-certificate
New-Item -ItemType Directory -Path Data -Force | Out-Null
Copy-Item config/database.example.json Data/database.json
```

Chỉ sao chép mẫu khi máy mới chưa có cấu hình riêng. Với database cấu trúc 5
đang có dữ liệu, đọc quy trình nâng cấp trong `docs/BACKEND_SQL_SERVER.md`.
Không có `Data/database.json` thì chế độ mặc định dùng SQLite cục bộ.
Sau khi cài xong, chạy launcher bên dưới và nhập link từng camera trong Cài đặt.

GitHub chỉ chứa mã nguồn, mẫu cấu hình và mô hình YOLO kèm thông tin nguồn.
`Data/`, `artifacts/`, mật khẩu, ảnh/video camera, backup, phụ thuộc cài sẵn
và file build được loại bằng `.gitignore`. Dữ liệu vận hành không đi kèm clone.
Các bài regression dùng ảnh camera riêng cần bộ fixture cục bộ, xem
[hướng dẫn kiểm thử](tests/README.md).

## Chạy trên máy hiện tại

Nhấp đúp **Run-CameraSystem.cmd** để khởi động và mở giao diện, hoặc chạy:

```powershell
./tools/Start-CameraSystem.ps1
```

Chạy project bằng `dotnet run`/Visual Studio cũng tự khởi động camera, OCR
và API hồ sơ qua `CameraSystemStartupService`. Web chỉ nhận yêu cầu khi ba
dịch vụ sẵn sàng. Launcher `-ServicesOnly` không mở thêm web, tránh trùng cổng.
Nếu worker dừng trong lúc dùng, nút kết nối RTSP tự gọi API phục hồi của project
rồi thử lại một lần; không cần chạy lệnh riêng. Cách đăng ký hosted service dựa
trên [tài liệu ASP.NET Core](https://learn.microsoft.com/en-us/aspnet/core/fundamentals/host/hosted-services?view=aspnetcore-8.0).

Mở `http://127.0.0.1:5080/`. Trong **Cài đặt**, chọn camera và nhập RTSP riêng
cho từng ô. Link chỉ giữ trong RAM, không lưu vào mã nguồn, log hay SQLite.
Khởi động lại broker cần nhập lại link; tải lại trang vẫn giữ nguồn đang chạy.
B/C/E không tự dùng nguồn A. Video mặc định tốc độ 1×.

Launcher kiểm tra đủ web/camera/OCR/hồ sơ trước khi báo sẵn sàng. Các worker
chạy độc lập với terminal; log khởi động nằm ở `Data/service-logs/`.
Sau khi khởi động lại Windows cần chạy launcher lại. Nếu máy chủ dừng,
giao diện giãn lần thử lại 5/10/20/30 giây; nút làm mới vẫn thử ngay.
`ERR_CONNECTION_REFUSED` ở cổng 5083–5085 nghĩa là dịch vụ chưa lắng nghe,
chưa phải lỗi đăng nhập RTSP hay nhận diện biển số.

## Luồng vào–ra

- Camera đầu tiên xác nhận biển số: ghi cổng/ngày giờ vào, D có xe, hai đèn đỏ.
- Cùng camera thấy lại: bỏ lần lặp. Biển khác khi D bận: chờ ngoài D, chưa ghi giờ vào.
- Camera khác xác nhận cùng biển: ghi cổng/ngày giờ ra, D trống, hai đèn xanh.
- Cổng phân biệt theo A/B/C/E. Nhập cùng RTSP riêng ở A và B vẫn ghép ra tại B;
  không dùng URL/hash nguồn để chặn cổng ra.
- Xe chờ chỉ vào sau khi D trống và được đọc lại ở khung mới.
- Sau khi ra, biển vẫn xuất hiện liên tục không tạo lượt mới ngay.
- Nguồn dùng chung hoặc video file chỉ được xem/OCR, không ghi lượt xe thật.

Trang hồ sơ hiển thị riêng **Xe đang giữ vùng D**, kể cả lượt nằm trong Thùng rác.
Bảng hồ sơ, thống kê và trạng thái D được đọc trong cùng transaction SQL Server.
Xóa danh sách không chứng minh xe đã ra. Khi camera bỏ lỡ lượt ra, người vận hành
có thể chọn **Xác nhận xe đã ra**, nhập cổng, giờ UTC+7 và lý do. Hệ thống lưu
xác nhận thủ công riêng, không tạo ảnh ra hoặc gắn nhãn nhận diện camera.
Lượt chờ đã ẩn sẽ trở lại danh sách khi có khung mới xác nhận vào D lúc D trống.

Hồ sơ nằm trong SQL Server `QuanLyXe`; ảnh toàn cảnh/crop nằm ở
`Data/vehicle-records/evidence/`. `Data/database.json` chọn database cho cả
OCR và API. SQLite cũ được giữ lại để đối chiếu; runtime SQL không ghi vào đó.
Backup lúc chuyển nằm ở `Data/backups/`.
Các bảng/cột SQL Server dùng tên tiếng Việt không dấu, ví dụ
`dbo.LuotXeRaVao`, `dbo.LanGhiNhanCamera`, `BienSo`, `CongVao`, `ThoiGianRa`.
Chỉ đổi tên; mã trạng thái và dữ liệu giữ nguyên. Mở
`docs/QUAN_LY_XE_KIEM_TRA.sql` để kiểm tra bằng SSMS.

Không cần chọn hướng camera hoặc loại dữ liệu. Các nút/kịch bản thử và lịch
sử OCR từng camera đã bỏ. **Hồ sơ vào–ra** là nơi xem lịch sử tổng và ảnh gốc.
Giao diện chỉ có trắng/đen, chuyển bằng một click, bốn camera nằm trong lưới.

**Xóa hồ sơ:** mở Hồ sơ vào–ra, bấm **Xóa** ở dòng xe, xác nhận **Xóa hồ sơ**.
Lấy lại tại **Thùng rác → Khôi phục**; ảnh và ngày giờ/cổng giữ nguyên.
Xóa không coi xe đã ra: lượt INSIDE vẫn giữ D/đèn đỏ đến khi camera khác
đọc đúng cùng biển số. Dòng dưới sơ đồ báo cổng vào và cổng ra mới nhất.

## Mã nguồn và dữ liệu

| Thành phần | Vị trí |
| --- | --- |
| HTML, lưới camera và sơ đồ | `wwwroot/index.html`, `wwwroot/css/` |
| Hiển thị xe D và đèn | `wwwroot/js/app.js` |
| Khung ảnh/nhận diện từng camera | `wwwroot/js/recognition.js` |
| Cài đặt RTSP riêng | `wwwroot/js/settings.js`, `tools/camera_streams.py` |
| YOLO → PaddleOCR PP-OCRv4 ONNX | `tools/local_ocr.py`, `tools/paddle_plate_ocr.py` |
| Đồng thuận ba khung thực tế | `tools/plate_tracking.py` |
| Hồ sơ, trạng thái D, ảnh và transaction | `tools/vehicle_records.py` |
| SQL Server adapter và chọn nguồn | `tools/sqlserver_store.py`, `tools/record_storage.py` |
| Schema và chuyển dữ liệu | `tools/sqlserver_schema.sql`, `tools/setup_sqlserver.py` |
| Tên bảng/cột tiếng Việt và nâng cấp | `tools/sqlserver_vietnamese.py`, `tools/localize_sqlserver.py` |
| API hồ sơ và Thùng rác | `tools/vehicle_record_server.py` |
| Trang hồ sơ và ảnh chi tiết | `wwwroot/js/vehicle-records.js` |
| Cấu hình đang dùng | `Data/database.json` |
| Ảnh trên máy và SQLite source cũ | `Data/vehicle-records/` |

Thời điểm lưu UTC và hiển thị UTC+7. Hai ảnh cho mỗi sự kiện: toàn cảnh và
crop biển số cùng khung; kèm SHA-256 để đối chiếu. Ảnh được xử lý cục bộ.
Không tự đoán ký tự biển hoặc dùng danh sách biển mẫu. OCR yêu cầu ít nhất
ba khung độc lập đồng thuận, confidence ít nhất 70%; ảnh mờ/che có thể chưa đọc được.

Chi tiết quy tắc, schema, API và kiểm thử: [Hồ sơ xe đợt 1](docs/HO_SO_XE_DOT_1.md).

## Dịch vụ và phụ thuộc

Web 5080; broker camera 5083; OCR 5084; hồ sơ 5085 — đều nghe loopback.
OCR và hồ sơ cập nhật độc lập để không phải ngắt nguồn RTSP đang chạy.
Cần VLC, FFmpeg và Python với phụ thuộc trong `artifacts/ocr-python-deps`.
SQL Server dùng ODBC Driver 18 và `pyodbc` trong `artifacts/sqlserver-python-deps`
(phiên bản ghi tại `tools/requirements-sqlserver.txt`).
Có thể đặt `CAMERA_PYTHON`, `VLC_EXE`, `FFMPEG_EXE` cho bản đã cài.
Tên biến runtime và lệnh khởi động cũ được giữ tương thích.

Máy chủ hiện tại chạy từ `artifacts/current-server/BridgeTrafficDemo.dll`;
HTML/CSS/JS được đọc từ mã nguồn project. Không thay đổi bảo vệ Windows.
Đèn/barrier vật lý chưa
được kết nối: trạng thái đèn của luồng mới hiện hiển thị trên sơ đồ.

Thông tin mô hình: `Models/license-plate-yolov8n.NOTICE.txt` và
`Models/PaddleOCR.NOTICE.md`.

## Kiểm thử

```powershell
$env:CAMERA_RECORDS_BACKEND='sqlite'
$env:CAMERA_SQLSERVER_TESTS='1'
python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/camera-polling.test.cjs tests/service-polling.test.cjs
Remove-Item Env:CAMERA_RECORDS_BACKEND, Env:CAMERA_SQLSERVER_TESTS
```

Kiểm thử lưu hồ sơ dùng DB/thư mục tạm, không thêm lượt thử vào dữ liệu
ứng dụng. SQL integration tạo và dọn DB riêng `QuanLyXe_Test_<UUID>`.
Ảnh kiểm thử là ảnh camera thật đã chụp. Chưa triển khai nhập tay lượt vào,
tìm kiếm/lọc hoặc các màn hình của hai đợt tiếp theo.
