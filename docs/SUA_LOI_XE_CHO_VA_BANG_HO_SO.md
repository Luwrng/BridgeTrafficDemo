# Giờ vào lần nhận diện đầu và khung chức năng cố định

## Quy tắc đã chốt

- Camera đầu tiên xác nhận biển số: lưu ngay cổng và thời điểm đó làm giờ vào hồ sơ.
- Cùng camera đọc lặp: giữ giờ vào, ảnh và ID lượt ban đầu.
- Camera khác xác nhận cùng biển, khung mới hơn giờ vào: lưu cổng/giờ ra, trạng thái COMPLETE.
- Giờ ra trừ giờ vào là thời gian lưu lại; không phát sinh nhãn “Đã ra · cần kiểm tra”.
- D trống: xe đầu tiên giữ D; chỉ đèn A hoặc B theo cổng ưu tiên được xanh, hướng kia đỏ.
  Xe đó ra thì chuyển ưu tiên FIFO; không có hướng ưu tiên thì cả hai đỏ.
- D bận: xe khác vẫn có giờ vào hồ sơ nhưng WAITING ngoài D. Camera khác đọc xe chờ
  thì đóng đúng lượt chờ đó, không giải phóng D của xe khác đang ở trong.
- Xe chờ đến lượt FIFO, được chính cổng ban đầu đọc lại khi D trống thì chuyển INSIDE, giữ giờ vào
  hồ sơ ban đầu. Không ghi thêm ảnh/lượt vào chỉ vì khung xác nhận quyền vào D.

Giờ vào là thời điểm nhận diện đầu theo yêu cầu người vận hành, không phải thời điểm
vật lý đi vào vùng D. Trạng thái WAITING/INSIDE quản lý quyền chiếm D riêng. Chống
khung lặp, khung cũ, cùng biển sau khi ra, transaction và rollback ảnh được giữ.
Không thay đổi YOLO/PaddleOCR, nguồn RTSP riêng hoặc ngưỡng xác nhận.

API trạng thái có `gateLogicVersion: 5`, dịch vụ hồ sơ 4.3, schema database vẫn 7.

## Dữ liệu cũ

`tools/first_seen_policy.py` xem số ứng viên; `--apply` sao lưu SQL Server COPY_ONLY,
CHECKSUM, VERIFYONLY cùng ảnh/SHA và JSON trước khi bổ sung giờ vào từ bằng chứng
camera sớm nhất. Transaction dùng chung khóa với worker và so khớp toàn bộ snapshot
để từ chối áp dụng nếu dữ liệu đổi trong lúc backup.

Chỉ xử lý hồ sơ camera thiếu giờ vào WAITING hoặc trường hợp NEEDS_REVIEW do điều
kiện chờ cũ tự tạo; không sửa hồ sơ nhập/sửa tay. Giữ giờ ra, ID và các file ảnh.
Lần nhận diện đầu được phân loại là entry theo quy tắc mới, có audit trước/sau;
không tự đóng xe, không cấp quyền hoặc giải phóng D. Chạy lại không cập nhật lặp.

## Giao diện

Menu chỉ còn Hồ sơ vào–ra và Cài đặt camera. Đã bỏ mục nhập lượt thủ công,
xe đang bên trong và hồ sơ cần kiểm tra, cùng bộ lọc/nhãn/ô đánh dấu liên quan. Metadata cũ
trong database/API giữ tương thích, không xóa dữ liệu audit. Hồ sơ vẫn là bảng dòng,
cuộn ngang/dọc độc lập, cố định tiêu đề và hai cột đầu, giữ vị trí khi làm mới.

Bảng chức năng desktop luôn rộng đúng 1/3 workspace cho hồ sơ, cài đặt,
sửa hồ sơ đã có, xem ảnh và xác nhận. Không đổi chiều rộng theo chức năng. Ở màn hình điện
thoại, cùng bảng chuyển xuống dưới camera và dùng toàn bộ chiều rộng.

## Kiểm thử

`tests/test_waiting_transfers.py` dùng SQLite/SQL Server UUID riêng để kiểm tra nhiều
xe liên tiếp, giữ giờ đầu khi đọc lặp/chuyển từ chờ, ghép đúng cổng ra khi D còn xe
khác, khung trễ, chống trùng, hồ sơ ẩn, nhập/sửa và nâng cấp dữ liệu có audit/rollback.
Không ghi lượt thử vào QuanLyXe. `tests/serve_record_ui_fixture.py --rows 45` mở UI
cách ly 5086 để kiểm tra bảng cuộn/phân trang, không dùng database vận hành.

Quy tắc đèn mới: [Interlock A/B và FIFO](DEN_A_B_FIFO.md).
