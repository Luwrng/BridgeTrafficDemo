# Đèn và barrier thật qua Modbus TCP

Mở ☰ → **Đèn & barrier**. Hai cấu hình riêng cho bộ đèn A/B và barrier. Có thể
dùng cùng PLC nếu mỗi bộ có vùng Holding Register khác nhau. Không có chế độ
mô phỏng và không mặc định bất kỳ IP/địa chỉ ngõ ra nào.

## Điều kiện tương thích

Modbus TCP chỉ quy định cách đọc/ghi, không quy định ý nghĩa thanh ghi của mọi
hãng. Connector hiện tại yêu cầu kỹ thuật viên lập trình PLC theo bảng bên dưới,
hoặc bổ sung adapter cho bảng thanh ghi riêng của model. Barrier thường cần
được nối qua PLC/tủ có giao tiếp Modbus; không giả định motor barrier có Ethernet.

| Bộ | Holding Register lệnh (FC06) | Holding Register phản hồi (FC03) |
|---|---|---|
| Đèn A/B | 0: hai đỏ; 1: A xanh B đỏ; 2: B xanh A đỏ | 0/1/2: trạng thái ngõ ra tương ứng được PLC xác nhận |
| Barrier | 1: mở; 2: đóng; 3: dừng | 0: đóng hết; 1: mở hết; 2: đang mở; 3: đang đóng; 4: dừng giữa hành trình |

Mỗi bộ có Holding Register **heartbeat** riêng. Backend tăng 16-bit mỗi giây
khi cho phép điều khiển. PLC phải tự xử lý mất heartbeat, ví dụ sau 5 giây:
đèn về hai đỏ, barrier dừng chuyển động; không tự hạ thanh. Giá trị timeout và
trình tự chuyển hướng cần được kỹ thuật viên xác nhận theo hiện trường.
Với barrier, PLC nhận và chốt lệnh1/2/3 rồi tự xóa thanh ghi lệnh về0 (không có
lệnh mới). Không khởi động lại chuyển động ở mọi chu kỳ quét khi thanh ghi chưa
đổi. Thanh ghi lệnh đèn giữ0/1/2, xử lý chuyển hướng bằng khóa chéo trong PLC.

Barrier có một Discrete Input (FC02): **1 có vật cản, 0 thông thoáng**. Mất
cảm biến phải được PLC biểu diễn là có vật cản/lỗi, không tự coi là thông thoáng.
Địa chỉ UI dùng **offset từ 0**, không nhập nhãn 40001 như thể đó là offset0.
Ba thanh ghi lệnh/phản hồi/heartbeat phải khác nhau. DI là vùng nhớ riêng,
có thể trùng số offset với Holding Register. Unit ID từ 1 đến 247.

Phản hồi barrier phải do cảm biến vị trí/bo điều khiển xác nhận. Phản hồi đèn
phải lấy từ ngõ ra; muốn xác nhận bóng sáng thực sự cần phần giám sát điện tương
ứng. Không chép thanh ghi lệnh làm phản hồi. Echo FC06 chỉ xác nhận PLC nhận lệnh.

## Quy trình sử dụng

1. Nhập IP nội bộ, TCP port (thường502), Unit ID, các offset và xác nhận bảng lệnh.
2. **Lưu cấu hình** ghi vào `Data/equipment-modbus.json` (được Git bỏ qua).
3. **Kết nối** chỉ đọc thanh ghi và cảm biến. Không ghi ngõ ra/heartbeat.
4. **Cho phép điều khiển** gửi hai đỏ đối với đèn hoặc dừng đối với barrier,
   sau đó backend duy trì heartbeat. Giao diện vẫn hiển thị phản hồi PLC,
   không tự gán “đã mở” từ lệnh yêu cầu.
5. Đèn có nút A xanh/B đỏ, B xanh/A đỏ, hai đỏ, và **Theo luồng camera**.
   Không được cấp hướng trái với xe trong D/hàng chờ. Dữ liệu D lỗi không cấp xanh.
   Đổi hướng cần barrier đóng hoặc vẫn là đúng hướng xanh hiện tại.
6. Barrier mở bằng nút khi PLC đèn xác nhận xanh và quyền D còn hợp lệ. Đóng
   cần cảm biến thông thoáng. Dừng gửi lệnh dừng; không suy diễn đó là vị trí đóng.
7. Ngắt kết nối khi đã cho phép sẽ gửi yêu cầu hai đỏ/dừng rồi dừng heartbeat.
   Không tiếp tục hiển thị ngõ ra như thể còn đang được xác nhận.

Khởi động lại chỉ nạp cấu hình; **không tự kết nối, không tự cho phép điều khiển**.
Backend vận hành khi đóng bảng hoặc trình duyệt. Không có phím Enter mở toàn trang.
Không có API ghi lượt xe/giải phóng D trong connector.

## Triển khai thực tế

Hiện chưa có thiết bị khách để nghiệm thu vật lý. Cần kiểm tra bảng địa chỉ, tải
đèn, relay/contactor, cảm biến vị trí và chống kẹt theo model. Khóa chéo hai xanh,
chống kẹt, khoảng chuyển tiếp hai đỏ và watchdog phải có trong PLC/tủ điều khiển,
không phụ thuộc vào lịch poll1giây của phần mềm. Với hai đèn đỏ khi mất nguồn cần
phương án cấp điện phù hợp; phần mềm không thể bật đèn khi tủ mất nguồn.

Connector chỉ chạy backend loopback, kết nối PLC qua IPv4 mạng nội bộ. Dịch vụ
thiết bị dùng cùng worker hồ sơ5085 nhưng không ghi schema/hồ sơ/ảnh; có thread
và khóa riêng. Khi triển khai nhiều người dùng/mạng ngoài cần cơ chế xác thực,
phân quyền điều khiển và thiết kế mạng tương ứng trước khi mở cổng.

## API và kiểm tra

- `GET /api/equipment`: cấu hình/trạng thái thật hoặc chưa kết nối.
- `POST /api/equipment/configure`: `{device, config, confirm:true}`.
- `POST /api/equipment/connect`: `{device, confirm:true}`; chỉ đọc.
- `POST /api/equipment/action`: `{device, action, confirm:true}`.
- Device: `lights`, `barrier`; action: `enable`, `disconnect`, `red`, `greenA`,
  `greenB`, `auto`, `open`, `close`, `stop` (tùy bộ).

`python -m unittest discover -s tests -p test_modbus_equipment.py -v` sử dụng TCP
server riêng ở loopback/cổng ngẫu nhiên, đọc/ghi gói Modbus thật để kiểm tra parser,
ack, lỗi kết nối, chống ghi khi chỉ đọc, khóa ưu tiên D và cảm biến. Không kết nối
PLC thực và không truy cập DB ứng dụng. Đây là kiểm thử mã, không phải chế độ thử UI.
