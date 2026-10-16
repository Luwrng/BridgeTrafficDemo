# Kiểm thử và dữ liệu riêng

Mã kiểm thử được đưa lên GitHub. Ảnh camera, thông tin nhận diện và kết quả
chụp tại máy phát triển trong `artifacts/ui-check/` được giữ riêng, không
đưa lên repository. Không lấy ảnh/hồ sơ vận hành làm dữ liệu mẫu công khai.

Sau khi cài phụ thuộc theo README, có thể chạy các bài không cần fixture ảnh:

```powershell
python -m unittest discover -s tests -p test_plate_tracking.py
python -m unittest discover -s tests -p test_camera_sources.py
python -m unittest discover -s tests -p test_sqlserver_vietnamese.py -k VietnameseMappingTests
node --test tests/camera-polling.test.cjs tests/service-polling.test.cjs tests/record-editor-time.test.cjs
```

Bộ đầy đủ trong README cần fixture riêng `camera-live.jpg`, `plate-crop.jpg`
và các file ảnh/JSON được tham chiếu trong `test_paddle_plate_ocr.py`.
Không có fixture thì các bài đó báo thiếu file; không tự thay bằng ảnh giả.
Chỉ chuyển fixture qua kênh riêng khi có quyền sử dụng dữ liệu.

Đặt `CAMERA_RECORDS_BACKEND=sqlite` trước khi chạy bộ đầy đủ để các module
kiểm thử không mở database đang vận hành. SQL integration còn cần
`CAMERA_SQLSERVER_TESTS=1`, SQL Server Express và ODBC Driver 18; nó tạo/dọn
đúng database `QuanLyXe_Test_<UUID>`, không ghi vào `QuanLyXe`.
Backup SQL của bài nâng cấp nằm trong thư mục backup mặc định của SQL Server,
mang tên database kiểm thử để phân biệt với backup dữ liệu thật.

`test_record_management.py` kiểm tra nhập/sửa, ảnh bổ sung và rollback,
lọc UTC+7, danh sách xe chưa ra, cờ cần kiểm tra và xung đột khi camera cập
nhật lúc đang sửa. Giao diện thử ở 5086 dùng DB tạm và nhãn cách ly; không
đưa kết quả thử vào `QuanLyXe`.

`test_waiting_transfers.py` kiểm tra lỗi xe thứ hai chờ ở A nhưng lần kế
tiếp đã ở C, các lượt A → C liên tiếp, chống ghi trùng, khung trễ và bổ sung giờ vào từ bằng chứng cũ có audit/rollback. Chạy cùng các ca trên SQLite/SQL Server UUID riêng.
`serve_record_ui_fixture.py --rows 45` thêm nhiều dòng vào DB tạm tại
5086 để kiểm tra bảng cuộn và phân trang; không ghi database vận hành.
