# Hồ sơ xe vào–ra — đợt 1, cập nhật ngày 14/10/2026

Yêu cầu mới thay thế hai mục tách dữ liệu thử và cấu hình hướng: bỏ các chế
độ này khỏi giao diện. Xe vào–ra và D được xác định bằng biển số giữa các
camera. Giữ lưu ngày giờ, ảnh bằng chứng và nguồn RTSP riêng A/B/C/E.

## Quy tắc đã được xác nhận

| Tình huống | Hồ sơ | Sơ đồ D và đèn hai bên |
| --- | --- | --- |
| D trống, camera đầu tiên xác nhận biển số | Tạo lượt INSIDE; ghi cổng/ngày giờ vào | Hiện xe và biển số ở D; cả hai đèn đỏ |
| Camera vào tiếp tục thấy cùng biển | Không tạo thêm lượt/ảnh | Giữ D có xe, cả hai đèn đỏ |
| D có xe, camera nhận biển khác | WAITING: chờ ngoài D; lưu ảnh/thời điểm phát hiện, chưa có giờ vào | Không cho xe chờ chiếm D; đèn vẫn đỏ |
| Camera khác nhận lại biển của xe ở D, thời điểm mới hơn | Đóng đúng lượt COMPLETE; ghi cổng/ngày giờ ra và hai ảnh | D trống; cả hai đèn xanh |
| D vừa trống, xe đang chờ được đọc lại ở khung mới | Chuyển lượt WAITING thành INSIDE với giờ vào mới | D có xe; đèn đỏ |
| Biển vừa ra vẫn xuất hiện liên tục | Không tạo lượt vào mới; cần ít nhất 5 giây không nhận diện biển đó | Giữ trạng thái hiện tại |
| Không đọc được API hồ sơ | Hiện lỗi trạng thái | Đèn trên sơ đồ đỏ, không tự công bố D trống |

Cùng một link RTSP gán vào nhiều ô được nhận biết bằng hash nguồn; không
được dùng lần đọc ở ô thứ hai để kết luận xe đã ra. Hash không chứa tên đăng
nhập/mật khẩu. Không dùng URL RTSP làm metadata hoặc tên file bằng chứng.

Đây là trạng thái theo hồ sơ vào–ra đã yêu cầu, không phải cảm biến đo vị trí
xe ở D. Đèn/barrier vật lý chưa có kết nối trong dự án; thay đổi hiện tại cập
nhật đèn trên sơ đồ.

## Luồng mã nguồn

1. `camera_streams.py`: từng camera giữ RTSP riêng trong RAM; VLC/FFmpeg lấy
   JPEG thật. Status có revision và hash nguồn để loại nhầm nguồn/camera.
2. `recognition.js`: giữ lưới bốn camera, gửi khung hiện tại đến worker 5084.
   YOLO cắt vùng biển, PaddleOCR PP-OCRv4 ONNX đọc chữ và tracker yêu cầu ba
   khung độc lập đồng thuận, confidence ít nhất 70%. Không đổi thuật toán đọc.
3. `local_ocr.py:record_camera_passage`: chỉ ghi khung RTSP riêng đang phát,
   đúng revision/frame, ảnh mới không trễ quá 10 giây. File video chỉ xem/OCR,
   không tạo hồ sơ. Lỗi ổ lưu trả riêng, không làm mất kết quả OCR.
4. `vehicle_records.py:PassageRecorder`: loại frame lặp/sai thứ tự; chỉ nhận
   biển đã xác nhận. Không cần hướng hay vạch.
5. `VisitStore.record`: khóa transaction SQLite trước khi quyết định vào,
   ra hoặc chờ. Ảnh toàn cảnh và crop được ghi/flush/fsync trước khi commit.
   Nếu lỗi, rollback hồ sơ và dọn đúng file vừa tạo của transaction lỗi.
6. `vehicle_record_server.py`: API đọc hồ sơ, bằng chứng và trạng thái D;
   xóa có khôi phục chỉ chuyển danh sách vào/ra Thùng rác. Không có API tạo
   sự kiện vào–ra hoặc sửa cổng/thời gian/thiết lập hướng từ giao diện.
7. `app.js`: nhận trạng thái D từ API/worker, hiển thị xe/biển số, cổng vào–ra và đèn. Không
   còn gọi API kịch bản cũ. Revision ngăn phản hồi cũ đổi đèn về trạng thái sai.
8. `vehicle-records.js`: trang hồ sơ tổng, chi tiết và ảnh gốc. Chuyển trang
   giữ nguyên DOM camera, không ngắt nguồn. Không có lịch sử OCR riêng từng ô.

Các phần quan trọng có chú thích tiếng Việt ngay trong mã: thời điểm ghi
vào/ra, xe chờ, transaction, xử lý trùng, ghi ảnh và đối chiếu nguồn.

## Lưu dữ liệu

- `Data/vehicle-records/visits.sqlite3`: lượt xe, ngày giờ, cổng và trạng thái.
- `visits`: INSIDE/COMPLETE/WAITING; lượt chờ không có `entered_at`.
- `passages`: ảnh toàn cảnh/ảnh biển, loại sự kiện, thời điểm, confidence,
  tên camera và SHA-256 của cả hai ảnh.
- `gate_state`: revision của trạng thái D.
- `plate_release`: thời điểm nhận diện biển đã ra, ngăn khung còn thấy biển
  đó tạo một lượt vào ngay sau lượt ra; giữ qua restart.
- `evidence/`: tên ảnh UUID, không chứa biển số, RTSP hay mật khẩu.

Schema cũ được nâng cấp thêm cột/bảng, không xóa hồ sơ đã tồn tại. Các cột
phân loại và bảng hướng của schema cũ không còn điều khiển tính năng mới;
API chỉ phục vụ hồ sơ thật. Nếu DB cũ có nhiều xe INSIDE, giữ đèn đỏ và báo
cần đối chiếu thay vì chọn tùy ý một xe để đóng lượt.

Thời điểm lưu dạng UTC ISO có múi giờ; màn hình hiển thị UTC+7. RTSP chỉ giữ
trong RAM máy chủ camera. LocalStorage chỉ nhớ trắng/đen, không giữ dữ liệu xe.

## Cách chạy và kiểm tra

Chạy `tools/Start-CameraSystem.ps1`, mở `http://127.0.0.1:5080/`.
Tên lệnh cũ `Start-CameraDemo.ps1` chỉ chuyển tiếp để shortcut cũ vẫn chạy.

- Web 5080: static HTML/CSS/JS trên máy chủ ASP.NET hiện có.
- Broker 5083: nguồn và khung RTSP riêng.
- Worker 5084: YOLO/PaddleOCR; status có `recordingVersion: 2`.
- Hồ sơ 5085: status có `schemaVersion: 3`, `serviceVersion: 2.1`.

API đọc 5085:

- GET `/api/occupancy`: xe đang trong D, revision, số xe chờ.
- GET `/api/visits?offset=0&limit=30`: danh sách/phân trang; thêm `deleted=1` để xem Thùng rác.
- GET `/api/visits/{id}`: chi tiết cùng ảnh bằng chứng.
- GET `/evidence/{uuid}_scene.jpg` hoặc `{uuid}_plate.jpg`: ảnh thật có trong DB.

POST `/api/visits/{id}/trash` hoặc `/restore` với JSON `{"confirm":true}`
chuyển một hồ sơ vào/ra Thùng rác. POST tạo/sửa sự kiện vẫn trả 405.
CORS chỉ cho ứng dụng loopback tại 5000/5080/5082.
Server dùng exclusive bind trên Windows để không nhận lẫn phiên bản cũ.

Mở **Cài đặt → Camera A/B/C/E**, nhập link riêng rồi kết nối từng ô. B/C/E
không được tự gán nguồn A. Kết nối một ô giữ nguyên các ô khác. Camera/video
mặc định 1×; các tùy chọn xem video cũ vẫn dùng được nhưng không ghi lượt từ file.

Nút phóng to là icon trong ảnh. **Quay lại** trở về bốn camera. **X** đóng
Cài đặt. Icon trăng/mặt trời chuyển ngay trắng/đen, không mở bộ chọn màu.

## Kiểm thử và phạm vi đợt này

`test_vehicle_records.py` và `test_record_capture.py` dùng DB tạm, broker mock
và ảnh RTSP thật đã chụp, không thêm dữ liệu mẫu vào hồ sơ ứng dụng. Kiểm tra
vào–ra, xe chờ, camera khác/cùng nguồn, trùng, frame trễ, restart, ghi ảnh lỗi,
concurrency, quyền đọc ảnh, CORS và API chỉ đọc. Test OCR/tracker giữ nguyên.

Đã có bảng tổng ngày giờ vào–ra, thời gian lưu lại và ảnh bằng chứng. Nhập
tay, danh sách bên trong riêng, tìm kiếm/lọc và báo cáo theo ngày/tháng chưa
được triển khai trong đợt này. Quy tắc chống lặp tối thiểu đã được bổ sung
vì luồng D mới cần nó; không tự mở rộng sang các chức năng còn lại.

## Xóa và khôi phục hồ sơ

Mở **Hồ sơ vào–ra → Xóa** ở đúng dòng xe → xác nhận **Xóa hồ sơ**. Để lấy
lại, mở **Thùng rác → Khôi phục**. Ảnh và ngày giờ/cổng được giữ nguyên.
Hủy/X trong xác nhận không xóa dữ liệu. Không có xóa hàng loạt/tự xóa hồ sơ.

Cột `deleted_at` chỉ điều khiển danh sách. Nó không được dùng trong điều
kiện tính D, tìm lượt đang INSIDE hoặc ghép lượt ra. Vì vậy xóa hồ sơ đang
theo dõi không bật xanh: đúng biển đó phải xuất hiện ở camera khác mới ghi
ra. Lượt ở Thùng rác vẫn được cập nhật cổng/ngày giờ ra; khôi phục trả về
trạng thái mới nhất. Không xóa ảnh vật lý hoặc xóa vĩnh viễn từ giao diện.

Xác nhận ngày 14/10: không cố định A là cổng vào. Camera đọc đầu tiên là cổng
vào; camera khác đọc lại cùng biển là cổng ra. Cùng camera đọc lặp không tính
xe đã ra; biển khác khi D bận vẫn chờ ngoài D. `lastExit` trong snapshot D
giúp dòng dưới sơ đồ báo cổng ra ngay và còn sau khi tải lại trang.
