# Hồ sơ xe: mục 5–9 và menu chức năng

Màn hình **Camera giám sát** luôn giữ sơ đồ và bốn camera. Nhấn **☰** để
mở bảng chức năng bên phải: Hồ sơ vào–ra, Xe đang bên trong, Hồ sơ cần kiểm
tra, Nhập lượt xe và Cài đặt camera & RTSP. Chọn chức năng và thao tác ngay
trong bảng đó, không chuyển sang trang khác hoặc che camera bằng hộp thoại.
Nhấn ☰ lần nữa, dấu × của bảng hoặc Escape để thu gọn; biểu mẫu nhập dở
được giữ. Nút biểu tượng đổi trắng/đen nằm ngay cạnh ☰ trên thanh đầu trang.
Bốn camera dùng hết chiều cao panel, ngang chiều cao vùng sơ đồ; ảnh giữ
object-fit:contain để không méo hình. Không tạo lại node ảnh/kết nối RTSP.

## 5. Chống ghi trùng

Cơ chế hiện có được giữ: frame trùng/sai thứ tự không ghi lần hai; cùng
camera đọc lại xe đang trong D không tính lượt mới; cùng biển ở camera khác
ghi ra trên đúng lượt cũ. Sau khi ra, biển còn xuất hiện liên tục được
suppression; cần ngừng xuất hiện ít nhất 5 giây mới ghi lượt vào tiếp.

Nhập tay cũng chống gửi lại bằng `request_id`. Một biển và cùng thời điểm
vào không được bổ sung thành lượt thứ hai; người vận hành cần sửa lượt cũ.

## 6. Nhập tay và sửa thông tin

- Menu → **Nhập lượt xe thủ công**: biển số, cổng/giờ vào–ra, ghi chú,
  lý do nhập/sửa và đánh dấu riêng nếu cần kiểm tra.
- Hồ sơ → **Xem ảnh / hồ sơ → Sửa / bổ sung thông tin** để sửa hoặc bổ sung.
- Cần ít nhất một thời điểm vào hoặc ra; không cho ngày giờ tương lai,
  giờ ra trước/đúng giờ vào, cổng sai hoặc lý do dưới 5 ký tự.
- Có giờ vào, chưa có giờ ra: lượt giữ D và đèn đỏ như lượt camera. Không
  cho có thêm lượt đang trong D khi còn xe khác, kể cả lượt đã bị ẩn.
- Chỉ biết lượt ra: lưu hồ sơ cần kiểm tra thiếu lượt vào, không chiếm D.
- Lượt WAITING có thể sửa biển/ghi chú mà vẫn chờ; chỉ chuyển vào sau khi
  được bổ sung giờ vào hợp lệ và D trống.
- Phiên bản chỉnh sửa ngăn biểu mẫu cũ ghi đè lượt mà camera vừa cập nhật.
  Giờ gốc có mili giây được giữ khi chỉ sửa biển số/ghi chú.
- Ảnh bổ sung: tối đa 4 ảnh/lần, 2 MB/ảnh, JPEG/PNG/WebP. Chọn ảnh vào,
  ảnh ra hoặc ảnh chung. Giữ ảnh gốc; không đổi ảnh bổ sung thành lần đọc OCR.
- Mọi lần nhập/sửa có thời điểm, lý do, dữ liệu trước/sau. Lượt trong Thùng
  rác cần khôi phục trước khi sửa. Không có chức năng xóa ảnh bằng chứng.

## 7. Xe đang bên trong

Menu → **Xe đang bên trong** hiển thị mọi lượt INSIDE chưa có giờ ra,
kèm cổng, ngày giờ vào và đồng hồ thời gian lưu lại. Bao gồm cả lượt đã
ẩn trong Thùng rác để không làm mất dấu xe chưa ra. WAITING ở ngoài D
không xuất hiện trong danh sách này. Thời gian được cập nhật mỗi giây.

## 8. Tìm kiếm và lọc

Bảng chức năng → **Hồ sơ vào–ra**, mở **Tìm kiếm và bộ lọc**, chọn rồi **Áp dụng**:

- Một phần hoặc toàn bộ biển số, không phân biệt chữ thường/hoa, dấu chấm/gạch.
- Ngày hoặc tháng theo UTC+7, hoặc khoảng từ/đến; mốc từ được tính,
  mốc đến không được tính. Có thể chỉ chọn một đầu khoảng.
- Dựa theo giờ vào, giờ ra hoặc một trong hai; xe chờ chưa có giờ vào/ra
  được lọc theo thời điểm ghi nhận khi chọn “Ngày giờ vào hoặc ra”.
- Cổng vào hoặc ra, trạng thái và “Chỉ hồ sơ cần kiểm tra”.
- Kết hợp các bộ lọc, phân trang 30 lượt; số lượng thống kê theo kết quả lọc.
  Trạng thái D ở phía trên luôn là trạng thái thật toàn hệ thống.
- **Xóa bộ lọc** để trở lại danh sách. Bộ lọc cũng dùng được trong Thùng rác.

## 9. Báo cáo thiếu dữ liệu ngay trong lịch sử

Nhãn **Cần kiểm tra** và lý do được tính từ dữ liệu hiện có: thiếu cổng/giờ,
thiếu ảnh vào/ra, lượt chưa đủ thông tin hoặc lý do đánh dấu của người dùng.
Xe đang trong D chưa có lượt ra là bình thường, không chỉ vì vậy mà báo lỗi.

Menu **Hồ sơ cần kiểm tra** dùng chính trang lịch sử cùng bộ lọc review;
không tạo bộ hồ sơ thứ hai. Bổ sung thông tin/ảnh tương ứng sẽ tự bỏ lý do
thiếu đó. Xóa nội dung “Đánh dấu cần kiểm tra” khi đã xử lý cờ thủ công;
không che được phần thiếu dữ liệu thực tế còn lại.

## Backend, database và API

- `tools/record_management.py`: nhập/sửa, ảnh, audit, kiểm tra xung đột,
  truy vấn lọc và lý do cần kiểm tra; dùng chung transaction với camera.
- `LuotXeRaVao` thêm `GhiChuBoSung`, `LyDoCanKiemTra`, `NguonTao`,
  `PhienBanChinhSua`. Mã trạng thái cũ giữ nguyên.
- `NhatKySuaHoSo`: lịch sử trước/sau và lý do. `AnhBoSungHoSo`: ảnh nhập tay,
  vai trò vào/ra, SHA-256 và chú thích. Ảnh vật lý vẫn ở thư mục evidence.
- GET `/api/visits`: offset/limit/deleted và các bộ lọc `plate`, `day`, `month`,
  `from`, `to`, `date_basis`, `gate`, `entry_camera`, `exit_camera`, `status`, `review`.
- GET `/api/inside`: cùng bộ lọc, luôn bao gồm lượt đang bên trong đã ẩn.
- POST `/api/visits/manual`: yêu cầu `confirm:true`, `request_id` UUID 32 hex,
  thông tin lượt, `reason`, ảnh tùy chọn.
- POST `/api/visits/{id}/edit`: thêm `expected_version` khớp bản đang sửa.
  Cập nhật đồng thời trả 409 để người dùng đọc lại thay vì ghi đè.
- API chi tiết bổ sung `attachments`, `changes`, `needs_review`, `review_reasons`.
- Nâng cấp SQL 6 → 7 qua `tools/upgrade_records_v7.py --apply` khi dừng worker.
  Công cụ backup SQL COPY_ONLY/CHECKSUM + VERIFYONLY, sao lưu ảnh, đối chiếu
  từng dữ liệu cũ trước khi commit; không tạo hồ sơ thử trong database thật.

## Kiểm tra

Các bài quản lý hồ sơ dùng SQLite tạm và SQL Server `QuanLyXe_Test_<UUID>`.
Giao diện tương tác thử trên 5086 có nhãn **KIỂM THỬ CÁCH LY**, không nối camera
và không ghi database ứng dụng. Bước tải ảnh qua UI chưa được xác nhận khi
quyền tải file bị từ chối; lưu ảnh, MIME, SHA và rollback đã được kiểm tra ở backend.

Đợt này đã kiểm tra 110 bài Python và 12 bài Node; giao diện ở 1920×1080,
1366×768 và 390×844. Ở hai kích thước máy tính, panel camera và sơ đồ bằng
chiều cao; không tràn ngang, không có dialog modal che màn hình giám sát.
Nhập/sửa và báo cáo đã thử qua UI với DB tạm; DB thật chỉ được đọc/đối chiếu
sau nâng cấp, giữ nguyên trường dữ liệu cũ và trạng thái D.
