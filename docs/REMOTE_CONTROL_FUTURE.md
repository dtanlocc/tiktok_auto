# Remote browser control — ghi chú cho lần viết lại

## Trạng thái hiện tại

- Dashboard chỉ nhận ảnh qua WebSocket `/ws/screens`.
- Click vào thumbnail chỉ phóng to ảnh để xem.
- Không có API start/stop takeover, không pause tác vụ và không gửi chuột hoặc
  bàn phím vào trang.
- Luồng ảnh chạy độc lập với WebSocket log/trạng thái để frame JPEG base64 không
  làm chậm các sự kiện nghiệp vụ.

Quyết định này là chủ ý. Bản remote-control thử nghiệm truyền input qua wrapper
Playwright nên có độ trễ, tranh chấp với automation và không đảm bảo quyền điều
khiển được bàn giao đúng thời điểm.

## Khi viết lại engine/session layer

Remote control chỉ nên được bật lại khi có đủ các phần sau:

1. **Control lease theo session**
   - Mỗi lần takeover có `session_id`, `lease_id`, thời hạn và quyền sở hữu rõ ràng.
   - Input sai lease hoặc tới sau khi phiên đã resume phải bị từ chối.

2. **Pause có xác nhận từ action runner**
   - Runner hoàn tất action nguyên tử hiện tại rồi chuyển sang `PAUSED_FOR_CONTROL`.
   - Không dựa vào sleep, timeout hoặc suy đoán rằng page đã đứng yên.

3. **Transport tách biệt**
   - Frame stream: một chiều, có backpressure và được phép bỏ frame cũ.
   - Input: kênh tin cậy, có sequence number, ACK và giới hạn tốc độ.
   - Log/trạng thái nghiệp vụ: kênh riêng, không mang dữ liệu ảnh.

4. **Ánh xạ hình học chính xác**
   - Frame phải kèm viewport, device scale factor, crop rectangle và frame id.
   - Click chỉ hợp lệ khi tham chiếu đúng frame/geometry đang hiển thị.

5. **Tiếp tục an toàn**
   - Trước khi resume phải nhả toàn bộ phím/nút chuột đang giữ, đồng bộ URL/page
     hiện tại và cho workflow đánh giá lại bước đang làm.
   - Các bước upload/post cần idempotency key để không đăng trùng sau takeover.

6. **Bảo mật**
   - Mặc định chỉ bind localhost; truy cập từ xa cần authentication và CSRF/origin
     policy riêng.
   - Có audit log cho người mở/đóng takeover và các input quan trọng.

## Nhận diện trình duyệt

Giao diện sản phẩm dùng icon/nhãn trung tính và không hiển thị tên engine. Stream
dùng `page.screenshot()`, nên chỉ chứa nội dung trang, không chứa tab bar, address
bar hay logo của trình duyệt.

Không đổi User-Agent thành tên một engine khác chỉ để “giấu” engine. UA, TLS,
WebGL, font và hành vi engine phải nhất quán; đổi riêng một trường sẽ làm browser
dễ bị nhận ra hơn. Nếu sau này cần một tên/biểu tượng riêng ở cấp executable, phải
thực hiện trong quy trình build engine và kiểm thử lại toàn bộ fingerprint.
