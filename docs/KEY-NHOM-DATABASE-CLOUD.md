# Key kích hoạt, database theo nhóm, điều khiển từ xa — ghi chú nghiên cứu

Viết 04/10/2026. Mọi con số trong tài liệu này là **đo trên repo**, không phải
ước lượng; chỗ nào là đề xuất thì ghi rõ là đề xuất.

## 1. Phần lớn "gắn key active" đã có sẵn

Đây không phải việc làm từ đầu. Repo đã có một control plane ở mức production:

| Đã có | Ở đâu |
|---|---|
| Service license: activation, renewal, release-check, download | `control_plane/app/` |
| Bảng `LicenseRecord`: `plan`, `features_json`, `max_accounts`, `max_tabs`, **`max_devices`**, `channel`, `expires_at`, `revoked_at` | `control_plane/app/models.py` |
| Bảng `DeviceRecord` theo từng thiết bị, `UsedNonce`, `UsedDownloadGrant`, `ReleaseRecord`, `AuditEvent` | cùng file |
| Client kiểm lease Ed25519: `license_id`, `device_id`, `plan`, `features`, `max_accounts`, `max_tabs`, `issued_at`, `expires_at`, `jti` | `backend/app/security/license.py` |
| Key khách **chỉ lưu dạng HMAC có pepper**, không lưu key thô | `control_plane/README.md` |
| Renewal phải chứng minh bằng khoá Ed25519 **của riêng thiết bị** | như trên |
| Grant tải artifact **dùng một lần**, không nằm trong URL/log | như trên |
| CLI tạo/thu hồi license, đăng ký artifact đã ký | `scripts/control_plane_admin.py` |
| Triển khai: Postgres riêng tư, Caddy TLS, chạy read-only, drop capabilities | `deploy/control-plane/` |
| Mô hình đe doạ + quyết định kiến trúc | `docs/hardening/commercial-client-protection-20260904/` |

Proposal đã **chốt Option 2 — hybrid online control plane**: trình duyệt chạy
local, service nắm quyền; và nó tuyên bố thẳng rằng **máy khách không đáng tin**
("a local attacker ... can observe the network contract, replay calls, patch a
future `if`"). Mọi thiết kế bên dưới phải tôn trọng câu đó.

**Còn thiếu để thật sự dùng được:** backend hiện chạy với `LICENSE_*` để trống,
nên không có rào nào; `friends_entrypoint.py` cố ý không cần key, còn
`secure_entrypoint.py` là bản có license. Nghĩa là việc còn lại là **vận hành**
(dựng control plane, phát key, build bằng đường `prepare_production_release.ps1`)
chứ không phải viết thêm cơ chế.

## 2. Nhóm dùng chung: đã có hai nửa, thiếu nửa thứ ba

- **N máy trên một key**: có sẵn — `max_devices` + `DeviceRecord`.
- **Chỗ đặt nhóm và quyền**: có sẵn — `features: tuple[str, ...]`, được **ký
  trong lease** nên client không sửa được.

  ⛔ **Sửa so với bản đầu của tài liệu này:** tôi viết `group:<id>` là **sai** —
  `_FEATURE_RE` là `^[a-z][a-z0-9._-]{1,63}$`, chưa bao giờ cho dấu hai chấm,
  nên cách viết đó **không ký được**. Đã làm, dùng dấu chấm: `group.<id>` và
  `role.<name>`, khớp luôn quy ước features đang dùng (`accounts.manage`,
  `upload.video`).

  Và nhóm/quyền **buộc** phải đi trong `features` chứ không phải vì tiện:
  `LeaseClaims` là `extra="forbid"` và `frozen`, nên thêm một claim mới đòi
  protocol v2 và làm vô hiệu mọi lease đã phát.

- **Đã làm (05/10/2026)**: `LeaseClaims.group_id` và `.role` đọc token từ lease.
  Hai token cùng loại, hoặc suffix rỗng/sai dạng, bị **từ chối ngay ở
  `verify()`** — không phải lúc đọc property — nên không chỗ nào trong app giữ
  một lease đã-tin-một-nửa. Có `group_feature()` / `role_feature()` để chỉ tồn
  tại một cách viết. 20 test trong `test_license_security.py`.
- **Thiếu**: bảng phân quyền theo `role:` (xem mục 7), và chỗ chứa dữ liệu chung.

## 3. Database lên cloud: chi phí thật, đo được

Tầng dữ liệu **không chỉ phụ thuộc URL**, nhưng cũng không bi đát:

| Đo | Kết quả |
|---|---|
| `sqlite_repository.py`, `schemas.py` | **0** câu SQL thô, **0** lần `.execute()` → đã sẵn sàng đổi hệ |
| `connection.py` | 276 dòng, **toàn bộ** chỗ dính SQLite nằm ở đây |
| Dính cái gì | `PRAGMA journal_mode/synchronous/busy_timeout/temp_store`, và **migration tự viết bằng `PRAGMA table_info`** |

Nên việc phải làm là: cho hook pragma biết dialect, và **thay migration thủ công
bằng Alembic**. Bounded, không phải viết lại. `psycopg[binary]` đã là dependency.

### Hai cách cho nhóm dùng chung — và cách nên chọn

**Cách A: client nối thẳng Postgres.** Nhanh làm nhất, nhưng phải phát credential
DB tới máy khách. Mâu thuẫn trực tiếp với mô hình đe doạ đã chốt: credential rút
được ra khỏi binary, và một thành viên đọc/ghi được **toàn bộ** dữ liệu của cả
nhóm, kể cả phần không thuộc quyền họ. Role trong lease trở thành trang trí.

**Cách B (đề xuất): một data-plane API đứng trước DB.** Client gửi lease đã ký,
API đọc `group:` và `role:` từ đó rồi mới cho đọc/ghi. Không credential DB nào ra
khỏi hạ tầng của mình. Đây đúng là Option 2 mở rộng sang dữ liệu, không phải một
kiến trúc mới.

**Người mua dùng riêng** thì không đổi gì: `DATABASE_URL` trỏ SQLite local như
hiện tại. Chỉ license có `group:` mới đi qua data plane. Một cờ, hai đường.

## 4. Excel / Google Sheets: không dùng làm database

Nói thẳng: ý này **không nên** làm nơi lưu dữ liệu chính, vì những lý do cụ thể
chứ không phải vì khẩu vị.

- **Quy mô**: hiện có **1.974 account**, mỗi account kèm cookie (blob JSON) và
  mật khẩu. Sheets không phải chỗ cho dữ liệu đó.
- **Không có transaction, không có lock dòng**: hai máy sửa cùng lúc là mất ghi,
  im lặng. Automation ghi liên tục thì chuyện này xảy ra hằng ngày.
- **Quota API**: đọc/ghi bị giới hạn theo phút; một phiên chạy nhiều account sẽ
  đụng trần.
- **Bí mật nằm trong một tài liệu chia sẻ**: mật khẩu và cookie của gần 2.000
  nick, bảo vệ bằng đúng một thiết lập chia sẻ. Dự án này **vừa** trả giá cho
  đúng loại sai sót đó: một khoá API nằm trong lịch sử repo public suốt hai
  tháng (xem commit `b7263d2`).

**Cách lấy được điều anh muốn mà không trả giá đó:** Postgres là nơi lưu thật,
rồi **đẩy một bản chiếu read-only sang Sheets để quan sát**, cột bí mật bị loại
ra. "Dễ quan sát" vẫn còn, mà bảng tính không còn là nguồn sự thật. Ai cần sửa
thì sửa trong app, không sửa trong bảng.

**Lưu key trong Sheets: cũng không.** Control plane đang cố ý chỉ lưu HMAC có
pepper của key — một bảng tính chứa key thô là tự bỏ đi lớp bảo vệ đó. Việc
tạo/thu hồi key đã có `scripts/control_plane_admin.py`.

## 5. Điều khiển từ điện thoại

`docs/REMOTE_CONTROL_FUTURE.md` đã liệt kê điều kiện để bật lại remote control:
control lease theo session, pause **được runner xác nhận** (không dựa vào sleep),
và transport tách riêng cho frame và input. Điều khiển từ điện thoại là đúng bài
toán đó cộng thêm một relay — nên nó **đi sau** những điều kiện kia.

Nhưng phần lớn nhu cầu "ở ngoài mà bấm được chức năng ở nhà" **không cần** điều
khiển trực tiếp. Nó cần: xem trạng thái, và **xếp việc vào hàng đợi**. Cái đó
làm được ngay trên data-plane API ở mục 3, không cần chạm vào input stream, và
không mang theo bất kỳ rủi ro tranh chấp nào với automation đang chạy.

## 6. Thứ tự tôi đề xuất

1. **Vận hành key trước, không viết thêm code.** Dựng control plane, phát một
   key thật, build bằng `secure_entrypoint`. Việc này xác nhận cả chuỗi đã có
   hoạt động — trước khi xây gì mới lên trên nó.
2. **Đọc `features` trong app** và chặn theo `role:`. Nhỏ, và là tiền đề của mọi
   thứ còn lại.
3. **Alembic thay migration `PRAGMA table_info`.** Phải làm trước Postgres, và
   dù sao cũng nên làm.
4. **Data-plane API + Postgres cho license có `group:`.** Local vẫn SQLite.
5. **Bản chiếu read-only sang Sheets** để quan sát.
6. **Xem trạng thái và xếp việc từ điện thoại** qua data plane.
7. **Remote control thật** — chỉ sau khi đủ ba điều kiện trong
   `REMOTE_CONTROL_FUTURE.md`.

## 7. Quyết định: đã chốt và còn mở

**Đã chốt 05/10/2026 — một DB chung, tách theo `group_id`.** Vận hành rẻ hơn một
Postgres cho mỗi nhóm. Cái giá phải trả, và phải trả có ý thức: cách ly giờ là
**thuộc tính của code**, không còn là thuộc tính của hạ tầng. Một câu query thiếu
điều kiện `group_id` là một lần rò dữ liệu giữa hai khách hàng, và không có
tường nào bên dưới chặn hộ. Hệ quả bắt buộc:

- Không tầng nào trên repository được tự viết query. Lọc theo `group_id` phải
  nằm ở **một chỗ** mà mọi truy cập đi qua, không phải nhắc nhau nhớ thêm `where`.
- Phải có test chứng minh một nhóm **không đọc được** dữ liệu nhóm khác, và test
  đó phải chạy mỗi lần push — vì đây là loại lỗi không ai nhìn thấy khi nó xảy ra.
- Nếu sau này cần cách ly mạnh hơn cho một khách lớn, cột `group_id` vẫn là
  đường di trú sang DB riêng; chọn chung bây giờ không khoá đường đó.

**Còn mở:**

- `role.` gồm những quyền gì cụ thể? ("viewer xem được account nhưng không thấy
  mật khẩu" là một ví dụ cần chốt rõ.) Và **một lease có `group.` mà không có
  `role.` thì xử thế nào** — từ chối thì lộ ngay một key phát sai, mặc định về
  quyền thấp nhất thì khách vẫn chạy được. Đoán quyền cao nhất là đường duy nhất
  chắc chắn sai.
- Khi mất mạng thì license có `group:` được chạy tiếp bao lâu? Proposal nói
  "short offline grace is acceptable" nhưng chưa định lượng.
- Người dùng riêng có bao giờ cần chuyển lên nhóm không? Nếu có thì cần đường di
  trú dữ liệu local → cloud.
