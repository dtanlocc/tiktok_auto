# Lệnh điều khiển — tài liệu chi tiết

23 lệnh, chạy bằng dòng lệnh không cần mở giao diện. Đây là nền móng cho bot
Telegram/mail sau này: mặt trước nào cũng gọi đúng những lệnh này.

```bash
./.venv/Scripts/python.exe scripts/tkauto_bot.py <lệnh> [tham_số=giá_trị ...]
```

Không tham số → in ra danh sách lệnh.

---

## Hai loại lệnh, và vì sao phải phân biệt

| Loại | Dấu hiệu | Cần backend đang chạy? |
|---|---|---|
| **Đọc** | không có gì | Phần lớn **không** — truy vấn thẳng database |
| **Sửa dữ liệu** | `[SỬA DỮ LIỆU]` | Có |

**Lệnh sửa dữ liệu bị từ chối cho tới khi thêm `--xac-nhan`.**

> Lý do: tool này sẽ chạy trên VPS và điều khiển từ xa. Một lệnh đổi lô 2000
> nick hay dừng khẩn cấp không được nằm cách một cú gõ nhầm trong khung chat.
> Chặn nằm ở tầng dưới (`registry.run`), không phải ở giao diện — nên mặt
> trước nào cũng được bảo vệ, kể cả mặt trước viết sau này.

## Mã thoát

| Mã | Nghĩa |
|---|---|
| 0 | Xong |
| 2 | Sai tên lệnh hoặc sai tham số |
| 3 | Lệnh sửa dữ liệu, thiếu `--xac-nhan` |
| 4 | Backend không chạy (lệnh hành động cần nó) |
| 5 | Backend từ chối |

Bot đọc mã thoát để biết nên báo gì, thay vì đoán từ chữ.

---

## Đích: lệnh chạy trên những nick nào

Mọi lệnh hành động đều nhận cùng một cặp `kieu` + `gia_tri`:

| `kieu` | `gia_tri` | Ví dụ |
|---|---|---|
| `tat_ca` | (không cần) | toàn bộ kho |
| `lo` | tên lô | `gia_tri="us sdt 9/11"` |
| `nuoc` | mã nước | `gia_tri=DE` |
| `nick` | username/email, phẩy ngăn cách | `gia_tri=nick1,nick2` |
| `loc` | `never` · `chua_co_so_lieu` · `failed` | `gia_tri=never` |

```bash
... tkauto_bot.py dong_bo kieu=lo gia_tri="us sdt 9/11" --xac-nhan
... tkauto_bot.py dong_bo kieu=loc gia_tri=never --xac-nhan
... tkauto_bot.py check_nhanh kieu=nuoc gia_tri=DE --xac-nhan
```

> **Đích rỗng là một lỗi, không phải "không làm gì".** Gõ nhầm tên lô thì lệnh
> từ chối kèm lý do, thay vì lặng lẽ chạy trên 0 nick để bạn tưởng đã xong.

Thêm một kiểu đích = thêm một dòng trong `targets.py`, không lệnh nào phải sửa.

---

## Lệnh nào chạy được từ xa

Mỗi lệnh có cờ `remote`. **Mặc định là không** — danh sách cho phép, không phải
danh sách cấm. Một lệnh mới thêm vào sẽ *không* tự nhiên chạy được trên máy
người khác; quên khai báo thì hậu quả là "không chạy được", chứ không phải
"chạy được trên máy khách mà không ai định".

Hiện **22/23 lệnh** cho phép từ xa. Riêng `gom_tren_nguong` giữ **tại chỗ** vì
nó đảo lô hàng loạt.

Hai phép kiểm **độc lập nhau**: cho phép từ xa *không* có nghĩa là đã xác nhận.
Một lệnh sửa dữ liệu chạy từ xa vẫn cần xác nhận.

---

# LỆNH ĐỌC

## `trang_thai`

Tổng quan toàn bộ kho nick.

```bash
... tkauto_bot.py trang_thai
```

Trả về: tổng nick · bao nhiêu có số liệu follower · phân bố theo trạng thái
đồng bộ (`NEVER`/`SUCCESS`/`PARTIAL`/`FAILED`) · theo sức khoẻ
(`ALIVE`/`BANNED`/`UNKNOWN`) · theo nước.

> Câu hỏi nó trả lời: *"Kho của tôi đang thế nào?"*

---

## `tren_nguong`

Nick đạt từ N follow trở lên.

| Tham số | Mặc định | Nghĩa |
|---|---|---|
| `nguong` | 1000 | Số follow tối thiểu |
| `nuoc` | (mọi nước) | Lọc một nước, ví dụ `US` |

```bash
... tkauto_bot.py tren_nguong nguong=1000
... tkauto_bot.py tren_nguong nguong=500 nuoc=DE
```

Trả về tổng, phân bố theo nước, và danh sách nick xếp theo follow giảm dần.

> ⚠️ Chỉ thấy nick **đã có số liệu**. Nick chưa đồng bộ không xuất hiện —
> không phải vì ít follow mà vì **chưa biết**. Chạy `dong_bo` trước.

---

## `nick`

Mọi thứ về một nick.

| Tham số | Nghĩa |
|---|---|
| `ten` | username hoặc email (nhận cả hai) |

```bash
... tkauto_bot.py nick ten=xoslan855
... tkauto_bot.py nick ten=montturos16483@hotmail.com
```

Trả về: nước, lô, sức khoẻ, follower, video, view, like, bình luận, chia sẻ,
proxy đang gán, lần cập nhật cuối, và **lý do đồng bộ hỏng** nếu có.

Không tìm thấy → `{"tim_thay": false, "ly_do": "..."}`, không phải lỗi.

---

## `danh_sach_lo`

Tất cả lô và tình trạng mỗi lô.

| Tham số | Mặc định |
|---|---|
| `gioi_han` | 30 |

```bash
... tkauto_bot.py danh_sach_lo gioi_han=10
```

Mỗi lô cho biết: tổng nick · bao nhiêu `ALIVE` · bao nhiêu đã đồng bộ.
Xếp theo số nick giảm dần.

---

## `nick_trong_lo`

Danh sách nick của một lô.

| Tham số | Mặc định |
|---|---|
| `lo` | (bắt buộc) |
| `gioi_han` | 50 |

```bash
... tkauto_bot.py nick_trong_lo lo="us sdt 9/11"
```

> Tên lô có dấu cách thì bọc trong nháy kép.

---

## `lo_hong`

Lô nào đang có nick đồng bộ hỏng, **kèm lý do đã gom nhóm**.

| Tham số | Mặc định |
|---|---|
| `gioi_han` | 10 |

```bash
... tkauto_bot.py lo_hong
```

Trả về mỗi lô: số nick hỏng và bảng đếm lý do. Dùng để biết *một lô hỏng cùng
một kiểu* (vấn đề hệ thống) hay *mỗi nick một kiểu* (vấn đề từng nick).

---

## `nick_hong`

Danh sách từng nick hỏng, kèm lý do đầy đủ.

| Tham số | Mặc định |
|---|---|
| `gioi_han` | 30 |

```bash
... tkauto_bot.py nick_hong gioi_han=50
```

---

## `proxy`

Toàn bộ proxy: host, cổng, giao thức, nước, trạng thái.

```bash
... tkauto_bot.py proxy
```

---

## `su_kien`

Nhật ký sự kiện gần nhất, mới nhất trước.

| Tham số | Mặc định | Nghĩa |
|---|---|---|
| `gioi_han` | 20 | Số dòng |
| `loai` | (mọi loại) | Lọc theo loại |

```bash
... tkauto_bot.py su_kien gioi_han=50
... tkauto_bot.py su_kien loai=sync.started
```

Loại đang ghi: `sync.started` · `login.started` · `queue.start` ·
`queue.pause` · `queue.resume` · `queue.stop` · `network.mode` ·
`proxy.allocated` · `accounts.promoted`

Nhật ký nằm ở `event-log/events-YYYY-MM-DD.jsonl`, mỗi dòng một JSON.
Đọc được qua SSH, và **vẫn còn khi backend chết** — đó là lý do nó là file
chứ không phải một bảng trong database.

---

## `gom_1k_xem_truoc`

**Xem trước** việc gom nick đạt ngưỡng sang lô riêng. Không đổi gì.

| Tham số | Mặc định |
|---|---|
| `nguong` | 1000 |

```bash
... tkauto_bot.py gom_1k_xem_truoc nguong=1000
```

Cho biết mỗi nước bao nhiêu nick, tên lô đích, và bao nhiêu nick **chưa có số
liệu** (nhóm này không được tính là "chưa đạt" — chúng chỉ là chưa biết).

> Cần backend đang chạy.

---

## `tinh_trang_chay`

Hàng đợi đang chạy thế nào: có đang chạy không, có tạm dừng không, bao nhiêu
tác vụ đang chạy/chờ.

```bash
... tkauto_bot.py tinh_trang_chay
```

> Cần backend đang chạy.

---

## `phien_ban`

Bản backend · thư viện trình duyệt · đời Firefox đang dùng.

```bash
... tkauto_bot.py phien_ban
```

> Cần backend đang chạy.

---

# LỆNH SỬA DỮ LIỆU

Mọi lệnh dưới đây **cần `--xac-nhan`** và **cần backend đang chạy**.

## `dong_bo`

Chạy đồng bộ số liệu.

| Tham số | Mặc định | Nghĩa |
|---|---|---|
| `kieu` | `loc` | Kiểu đích (xem phần Đích) |
| `gia_tri` | `never` | Giá trị của đích |
| `luong` | 4 | Số luồng |

```bash
... tkauto_bot.py dong_bo kieu=loc gia_tri=never --xac-nhan
... tkauto_bot.py dong_bo kieu=lo gia_tri="us sdt 9/11" --xac-nhan
... tkauto_bot.py dong_bo kieu=nuoc gia_tri=DE luong=6 --xac-nhan
```

⏱ ~10 giây/nick với 4 luồng.

---

## `dang_nhap`

Đăng nhập hàng loạt cho một lô.

| Tham số | Mặc định | Nghĩa |
|---|---|---|
| `kieu` | `lo` | Kiểu đích |
| `gia_tri` | (bắt buộc) | Giá trị của đích |
| `cach` | `COOKIE` | `COOKIE` hoặc `CREDENTIAL` |
| `luong` | 8 | Số phiên đồng thời |

```bash
... tkauto_bot.py dang_nhap kieu=lo gia_tri="us sdt 9/11" --xac-nhan
... tkauto_bot.py dang_nhap kieu=nick gia_tri=nick1,nick2 cach=CREDENTIAL --xac-nhan
```

`CREDENTIAL` là đăng nhập form + OTP — tốn OTP, chậm hơn, nhưng vào được khi
cookie đã chết.

---

## `check_nhanh`

Kiểm tra sống/chết cho một lô.

| Tham số | Mặc định |
|---|---|
| `kieu` | `lo` |
| `gia_tri` | (bắt buộc) |
| `luong` | 5 |

```bash
... tkauto_bot.py check_nhanh kieu=lo gia_tri="die-prx" --xac-nhan
... tkauto_bot.py check_nhanh kieu=nuoc gia_tri=US --xac-nhan
```

---

## `gom_tren_nguong`

Chuyển nick đạt ngưỡng follow sang lô riêng, **tách theo nước và đóng dấu
ngày**: `1K-US-2026-11-13`.

| Tham số | Mặc định |
|---|---|
| `nguong` | 1000 |

```bash
... tkauto_bot.py gom_tren_nguong nguong=1000 --xac-nhan
```

> **Chạy lần hai không đóng dấu ngày mới lên nick cũ.** Ngày trong tên lô là
> ngày nick *đạt ngưỡng*; nếu mỗi lần chạy đều ghi đè thì lịch sử mất sạch từ
> lần thứ hai. Nick đã nằm trong lô ngưỡng đó bị bỏ qua.

Chạy `gom_1k_xem_truoc` trước để xem sẽ chuyển những ai.

---

## `bat_dau` · `tam_dung` · `tiep_tuc` · `dung_khan`

Điều khiển hàng đợi, không tham số.

```bash
... tkauto_bot.py bat_dau --xac-nhan
... tkauto_bot.py tam_dung --xac-nhan
... tkauto_bot.py tiep_tuc --xac-nhan
... tkauto_bot.py dung_khan --xac-nhan
```

`dung_khan` huỷ mọi tác vụ đang chạy, không chờ chúng xong.

---

## `mang`

Đổi chế độ mạng ngay lúc chạy.

| Tham số | Mặc định |
|---|---|
| `dung_proxy` | `true` |

```bash
... tkauto_bot.py mang dung_proxy=false --xac-nhan   # mạng thật
... tkauto_bot.py mang dung_proxy=true --xac-nhan    # dùng proxy
```

> ⚠️ "Mạng thật" = đi bằng IP của máy. Nếu máy bật VPN đổi IP mỗi kết nối,
> TikTok sẽ giết phiên đăng nhập.

Áp cho các phiên **mở sau đó**, không cắt ngang phiên đang chạy.

---

## `ten_dang_nhap`

Gõ hộp thư hay username vào ô "Email or username" của TikTok.

| Tham số | Mặc định |
|---|---|
| `kieu` | `email` |

```bash
... tkauto_bot.py ten_dang_nhap kieu=username --xac-nhan
```

Dù chọn gì, khi TikTok báo "Account doesn't exist" thì tool tự bấm lại bằng
tên còn lại — nên lựa chọn chỉ quyết định tên nào tốn lượt đầu.

---

## `phan_bo_proxy`

Tự phân bổ proxy cho account, cân bằng tải.

```bash
... tkauto_bot.py phan_bo_proxy --xac-nhan
```

---

# Vài mạch việc hay dùng

**Kho đang thế nào, có gì hỏng không**

```bash
... tkauto_bot.py trang_thai
... tkauto_bot.py lo_hong
```

**Lấp dữ liệu cho nick chưa đồng bộ, rồi gom nick 1k**

```bash
... tkauto_bot.py dong_bo chon=never --xac-nhan
# đợi chạy xong
... tkauto_bot.py gom_1k_xem_truoc nguong=1000
... tkauto_bot.py gom_tren_nguong nguong=1000 --xac-nhan
```

**Một nick cụ thể có vấn đề**

```bash
... tkauto_bot.py nick ten=<username>
```

---

# Giới hạn hiện tại

**Chỉ chạy nội bộ.** Lệnh hành động gọi thẳng `http://127.0.0.1:9000`. Chữ ký
cục bộ chỉ bật ở chế độ `production`/`friends`, nên cách này **không điều
khiển được một bản đã cấp phép trên máy khách** — bí mật phiên do launcher
sinh ra và giữ trong bộ nhớ, không ghi ra đâu cả.

Đổi địa chỉ backend bằng biến môi trường:

```bash
TKAUTO_BACKEND_URL=http://127.0.0.1:9100 ... tkauto_bot.py tinh_trang_chay
```

Đây là việc phải giải riêng khi làm bot từ xa, không phải thứ làm tạm rồi quên.

**Thêm lệnh mới** = thêm một hàm có `@register(...)` trong
`backend/app/use_cases/control/commands.py`. Mọi mặt trước đều có ngay.
