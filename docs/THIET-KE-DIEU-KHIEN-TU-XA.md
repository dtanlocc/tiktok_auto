# Thiết kế: điều khiển máy khách từ xa

Bản thiết kế, **chưa code**. Mục tiêu: bạn ngồi ở máy mình (hoặc Telegram),
ra lệnh cho tool đang chạy trên máy bạn bè / VPS, và biết chắc lệnh tới đúng
máy, chạy đúng bản, trả kết quả về.

---

## 1. Ràng buộc có thật, phải thiết kế quanh nó

**Máy khách nằm sau NAT.** Bạn không mở được kết nối *vào* máy họ. Mọi thiết
kế kiểu "gửi lệnh tới máy X" đều sai ngay từ đầu.

→ Máy khách phải **hỏi xuống** máy chủ. Máy chủ là hộp thư, không phải cầu nối
trực tiếp.

**Máy khách đã gọi về máy chủ sẵn rồi** — gia hạn lease mỗi ~30 phút, và mỗi
lần mở app. Kênh đã có, chỉ cần gắn thêm việc.

---

## 2. Định danh: không cần bí mật mới

Đây là câu bạn hỏi — *"làm sao biết là đúng máy đó?"*. Hệ license đã trả lời:

```
device_id = sha256(khoá công khai của máy)
```

- Khoá riêng nằm trong Windows Credential Manager của **máy đó**, không rời máy
- Mỗi lần gọi về, máy ký một **device proof** (`issued_at` + `nonce` + chữ ký)
- Máy chủ đối chiếu với `public_key_pem` đã lưu từ lúc kích hoạt

Nên: **một máy không thể giả làm máy khác.** Không phải thêm "key bảo mật" nào
— thứ bạn muốn đã tồn tại từ khi bạn phát key cho họ.

Và vì `license.devices` nối `device_id ↔ license_id`, bạn luôn biết
*máy này thuộc key nào, của ai*.

---

## 3. Luồng một lệnh

```
BẠN                 MÁY CHỦ (Supabase)              MÁY KHÁCH
 │                        │                             │
 ├─ ra lệnh ─────────────►│                             │
 │   (CLI / Telegram)     │ lưu vào hàng đợi lệnh       │
 │                        │ trạng thái: CHỜ             │
 │                        │                             │
 │                        │◄──── hỏi: "có lệnh không?" ─┤  mỗi 30–60s
 │                        │      (kèm device proof)     │
 │                        ├──── trả lệnh ──────────────►│
 │                        │ trạng thái: ĐANG CHẠY       │  chạy lệnh
 │                        │                             │
 │                        │◄──── báo kết quả ───────────┤
 │                        │ trạng thái: XONG / HỎNG     │
 │◄─ đọc kết quả ─────────┤                             │
```

**Không có kết nối nào đi vào máy khách.** Mọi thứ do máy khách chủ động.

---

## 4. Bảng lệnh (một migration mới)

```
license.device_commands
  id              text        -- cmd_xxxxx
  device_id       text        -- gửi cho máy nào
  issued_at       bigint
  issued_by       text        -- 'operator' / 'telegram:<id>'
  command         text        -- tên lệnh, ví dụ 'dong_bo'
  params          jsonb
  min_backend     text        -- bản backend tối thiểu chạy được lệnh này
  status          text        -- CHO | DA_LAY | DANG_CHAY | XONG | HONG | HET_HAN
  claimed_at      bigint
  finished_at     bigint
  result          jsonb
  error           text
  expires_at      bigint      -- quá hạn mà chưa ai lấy thì bỏ
```

### Vì sao có `min_backend`

Bạn nêu đúng điểm này: *"cần phải đúng bản"*. Máy bạn bè có thể đang chạy
backend cũ hơn, chưa có lệnh đó.

→ Máy khách gửi `app_version` khi hỏi lệnh (nó **đã gửi sẵn** khi gia hạn
lease). Máy chủ chỉ trả những lệnh mà bản đó chạy được. Lệnh không hợp bản
được đánh `HONG` kèm lý do rõ: *"cần backend ≥ 0.1.25, máy đang chạy 0.1.22"*.

Không có cái này thì lệnh sẽ im lặng không làm gì, và bạn ngồi đợi một kết quả
không bao giờ tới.

### Vì sao có `expires_at`

Máy khách tắt máy đi ngủ. Lệnh "đồng bộ" ra lúc 2h sáng mà 9h mới có người bật
máy thì không nên chạy nữa. Quá hạn → `HET_HAN`, bạn biết nó không chạy chứ
không phải đang chạy.

---

## 5. Hai endpoint mới

**`POST /functions/v1/commands/poll`** — máy khách hỏi
- Gửi: device proof + `app_version`
- Nhận: tối đa N lệnh hợp bản, đánh dấu `DA_LAY`
- Máy chủ chỉ trả lệnh của **đúng device đã ký**

**`POST /functions/v1/commands/report`** — máy khách báo kết quả
- Gửi: device proof + `command_id` + kết quả/lỗi
- Máy chủ từ chối nếu `command_id` không thuộc device đó

Phía bạn đọc/ghi thẳng database bằng khoá operator — đã có sẵn.

---

## 6. Mô hình lệnh: phải nói rõ **làm gì**, **ở đâu**

Đây là chỗ bản hiện tại sơ sài. Lệnh bây giờ hoặc không có phạm vi, hoặc chỉ
nhận một lô. Mô hình đúng là **mọi lệnh hành động đều có một "đích"**:

```
lệnh  : dong_bo
đích  : { kieu: "lo",    gia_tri: "us sdt 9/11" }
        { kieu: "nuoc",  gia_tri: "US" }
        { kieu: "tat_ca" }
        { kieu: "nick",  gia_tri: ["a@b.com", "c@d.com"] }
        { kieu: "loc",   gia_tri: "never" }          # theo trạng thái
tham số: { luong: 4 }
```

Một `đích` dùng chung cho **mọi** lệnh: đồng bộ, đăng nhập, check sống/chết,
đổi profile, gom lô. Thay vì mỗi lệnh một kiểu chọn riêng như hiện nay.

### Bật/tắt

Bạn hỏi *"bật tắt như nào?"*. Hai loại khác nhau, đừng trộn:

| Loại | Lệnh | Phạm vi |
|---|---|---|
| **Hàng đợi** | `bat_dau` · `tam_dung` · `tiep_tuc` · `dung_khan` | Toàn máy |
| **Từng nick** | `tam_dung_nick` · `tiep_tuc_nick` | Theo `đích` |
| **Chế độ** | `mang` · `ten_dang_nhap` · `trinh_duyet` | Toàn máy, nhớ qua restart |

---

## 7. Những quyết định cần bạn chốt

### a) Lệnh nào được phép chạy từ xa?

Không phải lệnh nào cũng nên. Ví dụ `xoa_nick` từ xa là một cú gõ nhầm biến
thành mất dữ liệu của người khác.

Đề nghị: **danh sách cho phép** (allowlist), mặc định chỉ gồm lệnh đọc +
điều khiển hàng đợi + đồng bộ/check. Những lệnh đụng dữ liệu (xoá, chuyển lô,
thu hồi) phải khai báo riêng.

### b) Máy khách có được từ chối không?

Đề nghị **có một công tắc trong app**: *"Cho phép điều khiển từ xa"*. Mặc định
**tắt**, bạn bè tự bật.

Đây không phải chuyện kỹ thuật mà là chuyện giữa bạn và họ: đây là máy của họ,
tài khoản TikTok của họ. Một tool âm thầm nhận lệnh từ xa mà chủ máy không
biết là thứ khác hẳn với một tool họ chủ động bật.

### c) Bao lâu hỏi một lần?

| Nhịp | Độ trễ | Cái giá |
|---|---|---|
| 30 giây | gần như tức thì | ~2.900 lần gọi/máy/ngày |
| 2 phút | chấp nhận được | ~720 lần/ngày |
| Theo lease (30 phút) | quá chậm để điều khiển | miễn phí, đã có sẵn |

Đề nghị **60 giây khi app đang mở**, và chỉ theo lease khi nhàn rỗi.

### d) Kết quả giữ bao lâu?

Đề nghị 7 ngày rồi xoá. Kết quả đồng bộ 2000 nick có thể rất dài — nên lưu
**tóm tắt** trong `result`, chi tiết để lại trong nhật ký file trên máy đó.

---

## 8. Telegram/mail chỉ là lớp mỏng

Khi phần trên xong, Telegram **không cần biết gì về TikTok**:

```
tin nhắn Telegram → tên lệnh + đích + tham số → ghi vào bảng lệnh
kết quả trong bảng → tin nhắn trả lời
```

Và vì lệnh sửa dữ liệu đã đòi xác nhận ở tầng dưới (`registry.run`), bot
Telegram **không tự nới được** — nó phải hỏi lại người dùng.

---

## 9. Thứ tự làm

1. Mô hình `đích` cho lệnh hiện có (chạy nội bộ, chưa cần máy chủ)
2. Bảng lệnh + hai endpoint + vòng hỏi lệnh trên máy khách
3. Công tắc "cho phép điều khiển từ xa" trong app
4. Mặt trước Telegram

Bước 1 dùng được ngay và không cần phát hành gì. Bước 2–3 cần ra bản mới cho
máy khách.
