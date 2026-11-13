# TikTok Auto — toàn bộ chức năng

Tài liệu này liệt kê **những gì tool làm được**, viết từ chính mã nguồn
(5 router API, 75 endpoint, 5 thẻ giao diện, 32 script vận hành).

Cập nhật: 13/11/2026 · bản 0.1.22 · invisible_playwright 0.51.0 (Firefox 159)

---

## Kiến trúc: ba mảnh rời nhau

| Mảnh | Là gì | Cập nhật thế nào |
|---|---|---|
| **Launcher** (`TikTokAuto.exe`, 13 MB) | Vỏ ngoài + giao diện + kiểm license | Phải **gửi file .zip** |
| **Backend** (`backend-*.exe`, ~52 MB) | Toàn bộ logic, chạy ẩn | Máy khách **tự tải**, không gửi gì |
| **Máy chủ** (Supabase) | Phát key, phát bản, cấp lease | Bạn `publish`, hiệu lực ngay |

> Đổi backend → không gửi gì. Đổi giao diện → phải gửi zip mới.

---

## 1. Quản lý tài khoản

**Nạp vào**
- Nhập từ file (`import-file`) hoặc dán văn bản thô (`import-raw`)
- Nhập thủ công từng nick
- Mỗi nick gồm: email, mật khẩu, mật khẩu email, refresh token, client id, cookies

**Tổ chức**
- **Lô** (`batch_tag`) — gom nhóm tuỳ ý, cây thư mục bên trái
- **Nước** (`country`) — 21 nước, thêm nước mới là một dòng trong `countries.ts`
- Chuyển lô hàng loạt (`move-to-group`)
- **Gom nick đạt ngưỡng follow** sang lô riêng theo nước + ngày → `1K-US-2026-11-13`
- Lọc nhanh: ALL / IDLE / RUNNING / QUEUED / SUCCESS / ERROR
- Đánh dấu "đã bán" — nick đã bán bị bỏ qua ở mọi tác vụ

**Thao tác hàng loạt**
- Xoá nhiều, copy, xuất ra file, xoá cookies
- Gán proxy thủ công hoặc **tự phân bổ cân bằng**

---

## 2. Đăng nhập

Ba chiến lược, chọn trong menu chuột phải:

| Cách | Khi nào dùng |
|---|---|
| **Cookie** | Nhanh nhất, không tốn OTP |
| **Form + OTP** | Gõ tên + mật khẩu, tự lấy mã từ hộp thư |
| **Cookie rồi Form** | Thử cookie trước, hỏng mới chuyển OTP |

**Lấy OTP tự động** qua Microsoft Graph (hotmail) hoặc DongVanFB.

**Chọn gõ gì vào ô "Email or username"** — chuột phải → Đăng nhập Form + OTP → chọn *Gõ hộp thư trước* hoặc *Gõ username trước*. Lựa chọn được nhớ qua restart.

> Cả hai tên đều từng bị TikTok từ chối trong thực tế. Dù chọn gì, khi TikTok báo "Account doesn't exist" thì tool tự bấm lại bằng tên còn lại — nên lựa chọn chỉ quyết định tên nào tốn lượt đầu.

**Sau khi vào được**, tool đọc username thật từ trang Profile và ghi đè vào máy — nên username nhập sai tự được sửa.

---

## 3. Đăng video

**Nguồn video**
- Quét thư mục (`video-library/scan`)
- Chọn file lẻ hoặc cả thư mục qua hộp thoại Windows
- **Lô video** — gom video thành nhóm để phân phối

**Đăng**
- Đăng hàng loạt cho nhiều nick cùng lúc
- Đăng ảnh / media (`bulk-upload-media`)
- Caption lấy từ tên file, tự kích hoạt hashtag

**Hẹn giờ**
- Đặt lịch đăng video/ảnh, xem và huỷ lịch
- ⚠️ Dùng bộ hẹn giờ **của app**, không dùng "Lên lịch" của TikTok — chức năng đó bị khoá với nhiều nick

---

## 4. Tương tác video

- Chạy một lần hoặc **đặt lịch lặp**
- Tạm dừng / tiếp tục / xoá từng lịch
- Nạp file bình luận để tool chọn ngẫu nhiên

---

## 5. Đổi avatar & bio

- Hàng loạt từ một thư mục ảnh
- Tải ảnh lên qua giao diện
- Đồng bộ username thật về máy trong cùng lượt

---

## 6. Đồng bộ số liệu

Đọc **dữ liệu công khai** của nick: follower, video, view, like, bình luận, chia sẻ, và số liệu từng video.

**Chọn ai để đồng bộ**

| `select` | Nghĩa |
|---|---|
| `ids` | Danh sách bạn tích chọn |
| `never` | Chưa từng đồng bộ |
| `chua_co_so_lieu` | Chưa có số liệu follower |
| `failed` | Lần trước hỏng |

Tốc độ: ~10 giây/nick với 4 luồng.

**Khi hỏng**, lý do được ghi vào từng nick kèm **thông báo thật** của lỗi, không chỉ tên loại.

---

## 7. Kiểm tra sống/chết

- **Check nhanh** một lượt cho các nick đã chọn
- **Check liên tục** — vòng lặp nền, nghỉ N giây mỗi vòng
- Phân loại: ALIVE / BANNED / UNKNOWN

---

## 8. Proxy & mạng

- Nhập proxy từ file hoặc dán văn bản
- Hỗ trợ `http`, `https`, `socks5`
- **Kiểm tra proxy** — đo IP ra và quốc gia thật
- **Tự phân bổ** proxy cho account, cân bằng tải
- **Giới hạn đồng thời theo proxy** — mỗi IP chỉ N phiên cùng lúc
- **Chuyển "Mạng thật / Proxy" ngay lúc chạy**, không cần restart

> ⚠️ "Mạng thật" = đi bằng IP của máy. Nếu máy bật VPN loại đổi IP mỗi kết nối, TikTok sẽ giết phiên đăng nhập.

- Hỗ trợ extension NordVPN (bật/tắt trong app)

---

## 9. Điều khiển chạy

- Bắt đầu / Tạm dừng / Tiếp tục / **Dừng khẩn cấp** toàn cục
- Tạm dừng hoặc tiếp tục **từng account**
- Đặt số luồng tổng và số luồng mỗi proxy
- Hàng đợi tác vụ: `LOGIN_*`, `UPLOAD_VIDEO`, `UPLOAD_MEDIA`, `UPDATE_PROFILE`, `INTERACT_VIDEOS`, `SYNC_ANALYTICS`

---

## 10. Màn hình trực tiếp

- Xem ảnh trực tiếp của các trình duyệt nền đang chạy
- Tác vụ vẫn chạy khi bạn chuyển tab
- **Trình duyệt trắng** (`debug-blank`) — mở một trình duyệt sạch có extension, để thao tác tay
- **Debug login** — mở trình duyệt hiện để tự đăng nhập

---

## 11. Bot điều khiển (mới)

Chạy bằng dòng lệnh, **không cần mở giao diện** — dành cho máy VPS.

```bash
./.venv/Scripts/python.exe scripts/tkauto_bot.py
```

| Lệnh | Trả lời câu hỏi |
|---|---|
| `trang_thai` | Tổng quan: bao nhiêu nick, đồng bộ tới đâu, sống/chết |
| `tren_nguong nguong=1000 nuoc=US` | Nick nào đạt 1k follow |
| `nick ten=xoslan855` | Một nick cụ thể: mọi số liệu + lý do hỏng |
| `lo_hong` | Lô nào đang có nick hỏng, vì sao |
| `su_kien` | Nhật ký sự kiện gần nhất |
| `gom_tren_nguong nguong=1000 --xac-nhan` | **Sửa dữ liệu** — gom nick 1k sang lô riêng |

**Lệnh sửa dữ liệu bị chặn cho tới khi có `--xac-nhan`** — vì điều khiển từ xa nghĩa là một lệnh đổi lô 2000 nick chỉ cách một cú gõ nhầm.

**Nhật ký ghi ra file** `event-log/events-YYYY-MM-DD.jsonl`, mỗi dòng một JSON. Đọc được qua SSH, và **vẫn còn khi backend chết**.

> Telegram/mail sẽ là một lớp mỏng gọi đúng những lệnh này.

---

## 12. Giấy phép & phát hành (phía bạn)

**Giao diện quản lí key** — mở bằng:

```bash
./.venv/Scripts/python.exe scripts/key_manager.py --operator-directory D:/tkauto-operator-dev
```

- Phát key: đặt số account, số tab, **số máy**, số ngày, quyền
- Thu hồi license · Thu hồi thiết bị · **Gỡ thu hồi thiết bị**
- Xem bản phát hành, ngừng phát một bản

**Phát hành backend** (khách tự tải, không gửi gì):

```bash
./.venv/Scripts/python.exe scripts/supabase_release_admin.py --operator-directory D:/tkauto-operator-dev publish --file release/backend/0.1.22/backend-0.1.22.exe --version 0.1.22 --component backend --channel stable
```

**Công cụ cứu hộ** khi máy khách kẹt lúc tải engine Firefox:
`scripts/repair_engine_cache.ps1`

---

## Những ràng buộc đã học bằng sự cố thật

| Điều | Vì sao |
|---|---|
| Lease sống 1 giờ, backend đọc lại tệp liên tục | Trước đây backend chụp một lần → sau 1 tiếng mọi chức năng chết |
| Quyền của key phải khai đủ 5 mục | Mặc định cũ có tên quyền không tồn tại → mọi key chỉ đăng được video |
| Thu hồi *thiết bị* ≠ thu hồi *license* | Thu hồi thiết bị mà license còn sống → máy kẹt cả hai đường |
| Engine cũ không tự xoá | Mỗi lần nâng là +255 MB trên máy khách |
| Mỗi lần mở trình duyệt gọi GitHub một lần | invisible_playwright đếm lượt chạy, **không tắt được** |
| Một nick = một proxy IP tại một thời điểm | Quá giới hạn thì CDN từ chối, không phải cookies hỏng |
