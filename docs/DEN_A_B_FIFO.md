# Ưu tiên A/B và interlock

Chỉ A và B có đèn. Hai hướng không được đồng thời xanh, kể cả lúc D trống.

| Tình huống | A | B |
|---|---|---|
| Xe A được xác nhận đầu tiên, đang giữ D | Xanh | Đỏ |
| Xe B được xác nhận đầu tiên, đang giữ D | Đỏ | Xanh |
| Xe A đã ra, xe B là lượt chờ sớm nhất | Đỏ | Xanh |
| Xe B đã ra, xe A là lượt chờ sớm nhất | Xanh | Đỏ |
| Không có xe được ưu tiên, mất kết nối hoặc trạng thái xung đột | Đỏ | Đỏ |
| Xe giữ D/lượt chờ đầu ở C hoặc E | Đỏ | Đỏ |

C/E vẫn nhận diện và ghép lượt vào–ra; không có ánh xạ ngầm sang A/B.

`VisitStore._occupancy` tính `nextVehicle`, `greenGate`, `signals` trong cùng
snapshot với xe trong D và revision. Xe đang giữ D giữ quyền ưu tiên; khi D
trống chọn WAITING sớm nhất theo giờ ghi nhận đầu. Trùng thời điểm dùng cổng/ID
để quyết định ổn định. Lượt đã ẩn vẫn giữ quyền chiếm D/hàng chờ.

Xe chờ cần khung mới ở cổng ban đầu để được ghi INSIDE. Chỉ đúng lượt đầu
FIFO được chuyển vào; xe mới hoặc lượt đến sau không được chen khi D trống.
Xe chờ được camera khác ghi ra vẫn đóng đúng hồ sơ, không đổi đèn của xe khác
đang giữ D. Giờ vào hồ sơ vẫn là lần đọc đầu, không đổi vì phải chờ.

`gate-signals.js` kiểm tra dữ liệu ưu tiên hợp lệ rồi tắt cả hai xanh trước
khi bật một hướng. Mất dữ liệu, phiên bản cũ hoặc phản hồi hai xanh trả về hai
đỏ. Sơ đồ và tóm tắt hồ sơ dùng chung kết quả. Đèn hiển thị trên SVG; dự án
chưa kết nối PLC/đèn vật lý. Không đổi nguồn RTSP, OCR hay schema SQL Server 7.

API trạng thái: `gateLogicVersion=5`, dịch vụ hồ sơ 4.3;
snapshot có `signalPolicyVersion=1`. Không có migration sửa dữ liệu cho đợt này.

Kiểm thử `test_gate_priority.py` trên SQLite/SQL Server UUID riêng kiểm tra FIFO,
hai thứ tự A/B, cùng lúc, restart, hồ sơ ẩn và nhiều xe. `gate-signals.test.cjs`
kiểm tra interlock sau từng thay thuộc tính SVG, cả A → B lẫn B → A và mất kết nối.
Fixture UI 5086 dùng DB tạm; `/qa/exit`, `/qa/enter-next`, `/qa/exit-next` chỉ
có ở công cụ kiểm thử, không thêm nút/sự kiện giả vào ứng dụng vận hành.
