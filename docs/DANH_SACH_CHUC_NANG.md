# Chức năng Camera giám sát — 16/10/2026

## Đang có trên web

1. Bốn camera A/B/C/E, nguồn RTSP độc lập từng ô.
2. Nhập RTSP, kết nối/ngắt nguồn; link khóa khi đã cấu hình, chỉ mở nhập lại
   sau khi máy chủ xác nhận ngắt. Mất hình/mạng tạm thời không tự mở khóa link.
3. Báo đã kết nối khi nhận được hình; trạng thái đang chờ hình/mất liên lạc riêng.
4. Phóng to từng camera và quay lại lưới 2×2.
5. Camera bên trái, sơ đồ bên phải, tỷ lệ50/50 trên desktop. Bảng chức năng phủ
   sơ đồ khi mở; camera giữ kích thước. Responsive cho màn hình nhỏ.
6. Đổi nền trắng/đen bằng một nút.
7. YOLO tìm/cắt biển số → PaddleOCR đọc chữ, đồng thuận nhiều khung hình.
8. Camera đầu tiên xác nhận biển số tạo cổng/giờ vào; camera khác xác nhận cùng
   biển số ghi cổng/giờ ra trên đúng lượt. Không cần đặt cổng vào/ra cố định.
9. Chống ghi trùng khi cùng xe đứng lâu trước một camera; chặn khung cũ/lặp,
   hạn chế tạo lại lượt khi biển vừa ra vẫn còn liên tục trong ảnh.
10. Vùng D chỉ cho một xe giữ quyền; xe khác lưu hồ sơ chờ ngoài D. FIFO cấp
    lượt tiếp theo; xe chờ cần khung mới ở cổng ban đầu để được nhận vào D.
11. Đèn A/B trên sơ đồ: chỉ một hướng xanh theo xe được ưu tiên. D trống không
    có hướng ưu tiên hoặc dữ liệu không hợp lệ thì cả hai đỏ. C/E không tự ánh
    xạ ngầm sang A/B. Đây là hiển thị logic, không phải xác nhận đèn vật lý.
12. Hiển thị ngay trên sơ đồ: xe/biển số đang trong D và cổng vào; nhãn xe chờ
    tại cổng; nhãn đã ra tại cổng ra được server xác nhận. Không có thanh thông
    báo, nút chuông, trang thông báo mới, hoặc nội suy vị trí xe giữa hai camera.
13. Hồ sơ dạng dòng/bảng, cuộn ngang/dọc độc lập, giữ cột biển số và phân trang.
14. Lưu cổng/ngày giờ vào–ra, trạng thái lượt xe, thời gian lưu lại; hiển thịUTC+7.
15. Ảnh toàn cảnh và ảnh cắt biển số tại sự kiện vào–ra, xem trong chi tiết hồ sơ.
16. Tìm/lọc theo biển số, ngày, tháng, khoảng thời gian, cổng, trạng thái.
17. Sửa/bổ sung hồ sơ đã có, ghi chú, đính kèm ảnh; lưu lịch sử chỉnh sửa.
18. Xác nhận xe đã ra thủ công khi camera bỏ lỡ, kèm cổng/giờ/lý do riêng.
19. Xóa hồ sơ khỏi danh sách; dữ liệu/ảnh vẫn giữ, không tự giải phóngD.
20. SQL Server `QuanLyXe`, bảng/cột tiếng Việt không dấu; ảnh bằng chứng lưu
    trên máy. Có công cụ cấu hình/chuyển dữ liệu/backup theo quy trình dự án.
21. Khởi động web/camera/OCR/hồ sơ cùng project/launcher; thử lại có giãn nhịp
    khi dịch vụ chưa sẵn sàng. Link RTSP giữ trong RAM của worker.
22. Công cụ video riêng: phát/tạm dừng, tua, tốc độ, nhận diện khung dừng.
    Nguồn video file chỉ xem/OCR, không tạo hồ sơ vận hành thật.

## Đã chuẩn bị nhưng đang ẩn theo yêu cầu

Connector đèn/barrier Modbus TCP với cấu hình riêng, đọc trạng thái thật,
gửi lệnh và heartbeat ở backend. Chưa có thiết bị để nghiệm thu vật lý;
PLC phải được lập trình theo [bảng lệnh](KET_NOI_THIET_BI.md). Mục điều khiển
đèn/barrier bị ẩn trên giao diện, không tự kết nối hoặc điều khiển khi khởi động.

## Các mục đã bỏ khỏi giao diện

Thùng rác; tạo lượt nhập tay; trang Xe đang bên trong riêng; nhãn/bộ lọc “cần
kiểm tra”; lịch sử OCR riêng từng camera; nút kịch bản mô phỏng.

Chưa triển khai Vercel theo yêu cầu mới nhất. Bản đầy đủ chạy tại máy chủ nội bộ.
