# Key license trên Supabase

Đường này giải đúng ba thứ đang tắc: **HTTPS thật** (bắt buộc, không có ngoại lệ
cho localhost — `control_plane/app/config.py:validate_runtime`), **không cần
domain**, và **luôn online** mà không phải bật máy ở nhà. Tên miền
`*.supabase.co` mang chứng chỉ công khai, nên launcher tin nó mà không phải cài
CA nào vào máy khách.

Và nó không phải sửa một dòng Rust nào. Launcher dựng URL cứng là
`{base}/v1/activate`, `{base}/v1/lease/renew`, `{base}/v1/releases/check`
(`lib.rs`), trong khi Supabase định tuyến `/functions/v1/<tên>/...` về function
`<tên>` và giữ phần đường còn lại. Nên:

```
TKAUTO_CONTROL_PLANE_URL = https://<ref>.supabase.co/functions
```

khớp cả ba endpoint với ba function tên `activate`, `lease`, `releases`.

---

## 1. Cái gì nằm ở đâu

| Thứ | Nằm ở | Lên Supabase? |
|---|---|---|
| Key license (dạng rõ) | file trong thư mục operator, phát một lần | **Không** — DB chỉ có digest |
| `key_digest` = HMAC(pepper, key) | bảng `license.licenses` | Có |
| Pepper | `<operator>/secrets/license-key-pepper` **và** secret Edge Function | Có |
| Khoá ký **lease** | `<operator>/keys/lease-private.pem` **và** secret Edge Function | **Có** — xem mục 7 |
| Khoá ký **release** | `<operator>/keys/release-private.pem` | **Không, tuyệt đối** |
| Mật khẩu database | `<operator>/secrets/supabase-database-url` | — |

⛔ **Khoá release không bao giờ lên mây.** Chiếm được khoá lease chỉ là dùng lậu
app. Chiếm được khoá release là **gửi được bản cài độc cho khách**. Hai khoá đã
tách sẵn từ `initialize_operator_environment.py`; giữ nguyên như vậy.

---

## 2. Tạo project Supabase — **việc của anh**

Tôi không tạo tài khoản hộ được. Anh làm:

1. Vào <https://supabase.com> → tạo project (free tier là đủ).
2. Chọn region gần (Singapore).
3. Lưu mật khẩu database nó đưa — **không hiện lại lần thứ hai**.
4. Ghi lại **project ref**: đoạn trong URL dashboard,
   `https://supabase.com/dashboard/project/<ref>`.

Rồi lấy connection string: bấm nút **Connect** ở **đầu trang project** (cạnh
tên project), rồi chọn **Session pooler**.

⛔ **Không phải Project Settings → Database.** Bản đầu của tài liệu này ghi
đường đó và nó sai: Supabase đã chuyển chuỗi kết nối ra nút `Connect` ở thanh
trên. Nếu lần sau không thấy nút đó thì tra
<https://supabase.com/docs/guides/database/connecting-to-postgres> chứ đừng
tìm trong Settings.

Chọn **Session pooler** (cổng **5432**), không phải Transaction pooler (6543) và
không phải Direct connection. Direct connection là IPv6 với project mới, còn
Session pooler được proxy qua IPv4 miễn phí — trên máy này IPv6 không chắc
thông. Mật khẩu database chỉ hiện **một lần** lúc tạo project; mất rồi thì reset
ngay trong hộp thoại `Connect` (hoặc Database → Settings), và reset sẽ làm đứt
mọi kết nối đang mở.

Dán vào:

```
<operator>/secrets/supabase-database-url
```

Dùng Session pooler vì chế độ transaction (6543) không giữ phiên giữa các câu
lệnh, và áp schema thì cần phiên. CLI admin đã đặt `prepare_threshold=None` nên
cả hai cổng đều chạy được, nhưng cổng 5432 là cổng không có bẫy.

---

## 3. Áp schema

Hai cách, chọn một.

**Cách A — bằng Supabase CLI** (đã cài sẵn, ghim 2.120.0 ở
`tools/supabase-cli/package.json`):

```bash
tools/supabase-cli/node_modules/.bin/supabase login
tools/supabase-cli/node_modules/.bin/supabase link --project-ref <ref>
tools/supabase-cli/node_modules/.bin/supabase db push
```

**Cách B — không cần login, chạy thẳng qua Postgres:**

```bash
uv run python scripts/supabase_license_admin.py apply-schema --operator-directory D:/tkauto-operator-dev
```

Cả hai chạy ba file trong `supabase/migrations/`. Sau đó bảng nằm ở schema
`license`, **không phải `public`** — xem mục 7.

---

## 4. Đặt secret cho Edge Function

```bash
uv run python scripts/supabase_make_function_env.py --operator-directory D:/tkauto-operator-dev
```

Script đọc pepper, `kid` và khoá lease từ thư mục operator rồi ghi
`<operator>/supabase-functions.env`. Nó không in giá trị nào ra màn hình, và
khoá riêng được ghi dạng **base64 một dòng** — `importLeasePrivateKey` gỡ đầu
PEM và mọi khoảng trắng trước khi decode, nên một dòng thì không thể bị mất dấu
dòng khi đi qua `secrets set`, dashboard, hay một lần copy-paste. Một PEM mất
dấu dòng là function **chết lúc deploy**, không phải lúc test.

⛔ **File đó nằm ngoài repo, có lý do.** Bản đầu của tài liệu này bảo ghi vào
`supabase/.env`; đó là một bước lùi vì nó đặt khoá riêng ký lease **vào trong**
cây làm việc của git. File ấy có bị gitignore, nhưng "có bị gitignore" là một
lớp bảo vệ, còn "không bao giờ nằm trong repo" thì không cần lớp nào.
`supabase/.env.example` vẫn giữ làm bản liệt kê các biến.

Rồi đẩy lên:

```bash
tools/supabase-cli/node_modules/.bin/supabase secrets set --env-file D:/tkauto-operator-dev/supabase-functions.env
```

⛔ **Pepper phải khớp tuyệt đối** giữa `<operator>/secrets/license-key-pepper`
và secret trên Supabase. CLI admin bằm digest bằng cái thứ nhất, Edge Function
tra cứu bằng cái thứ hai. Lệch một ký tự thì **mọi key đều báo sai** và không
có thông báo nào nói lý do.

---

## 5. Deploy ba function

```bash
tools/supabase-cli/node_modules/.bin/supabase functions deploy activate lease releases
```

`supabase/config.toml` đã đặt `verify_jwt = false` cho cả ba. Đó là **bắt buộc,
không phải lỗ hổng**: launcher không gửi header `Authorization` nào, nên để mặc
định thì mọi request bị 401 trước khi vào function. Tắt nó không làm yếu gì, vì
anon key vốn được nhúng vào app khách — một bí mật nằm trong file `.exe` gửi cho
khách thì không còn là bí mật. Phần bảo vệ thật nằm ở bằng chứng thiết bị
Ed25519, digest key, chống phát lại nonce và chống dò tần số, không thứ nào phụ
thuộc header đó.

---

## 6. Build desktop và phát key

Build với:

```
TKAUTO_CONTROL_PLANE_URL = https://<ref>.supabase.co/functions
```

Phát một key:

```bash
uv run python scripts/supabase_license_admin.py create \
    --operator-directory D:/tkauto-operator-dev \
    --customer-reference ban-A --plan nhom \
    --group hn01 --role member \
    --features accounts.manage,upload.video \
    --max-accounts 200 --max-tabs 4 --max-devices 2 --expires-days 30
```

Key thật **không in ra màn hình** (màn hình đi vào log, ảnh chụp, lịch sử
terminal) — nó được ghi vào `<operator>/license-<khách>-<id>.json`. Gửi riêng
cho khách.

`--group hn01` thành feature `group.hn01`, `--role member` thành `role.member`.
Nhóm và quyền **đi bên trong `features`** chứ không phải claim riêng, vì
`LeaseClaims` là `extra="forbid"` và frozen — thêm field là phải đúc protocol v2
và làm vỡ mọi lease đã phát. `features` được ký cùng phần còn lại nên một token
đặt ở đó chống sửa y như một claim.

Xem tình hình:

```bash
uv run python scripts/supabase_license_admin.py list --operator-directory D:/tkauto-operator-dev
```

Thu hồi:

```bash
uv run python scripts/supabase_license_admin.py revoke --operator-directory D:/tkauto-operator-dev --license-id lic_... --reason "..."
uv run python scripts/supabase_license_admin.py revoke-device --operator-directory D:/tkauto-operator-dev --device-id device_...
```

⛔ **Thu hồi có hiệu lực ở lần gia hạn kế tiếp, không phải ngay.** Lease đã phát
được client kiểm **offline** bằng chữ ký, nên nó hợp lệ tới khi hết hạn — tối đa
một giờ với TTL mặc định. Muốn nhanh hơn thì hạ `TKAUTO_LEASE_TTL_SECONDS`
(sàn 300 giây), và trả giá bằng số lần gọi gia hạn.

### Xem từ điện thoại

Dashboard Supabase → **SQL Editor**:

```sql
select * from license.overview order by days_left;
```

View đó không chứa key và không chứa khoá công khai thiết bị. `group_feature` và
`role_feature` được **rút ra từ `features`** chứ không lưu riêng, nên không thể
lệch với thứ đã được ký.

---

## 7. Mô hình nguy cơ, nói thẳng

**Nếu Supabase bị chiếm** (hoặc service-role key lộ): người ta đọc được bảng
license và **ký được lease**, tức cấp được quyền dùng app. Người ta **không**
lấy lại được key license nào (chỉ có digest, pepper không nằm trong DB) và
**không** ký được bản phát hành (khoá release không ở đó). Đây là cái giá đã
chọn ở mục 1 để có kích hoạt tức thì.

**Anon key lộ là chuyện đương nhiên** — nó nằm trong file `.exe`. Vì thế bảng
license nằm ở schema `license`, **không phải `public`**: PostgREST chỉ phục vụ
`public`, nên ở `license` nó không nhìn thấy đường nào tới bảng — không phải "bị
từ chối" mà là "không tồn tại" từ góc nhìn API. RLS bật trên mọi bảng và không
có policy nào là lớp thứ hai, không phải lớp duy nhất.
`test_supabase_sql_is_parseable.py` có một bài chặn việc một `create table` sau
này vô tình rơi vào `public`.

**Hàm RPC bị gỡ quyền EXECUTE khỏi `anon`/`authenticated`.** Mặc định Postgres
cho PUBLIC gọi mọi hàm mới, và `public` là schema PostgREST phục vụ — không gỡ
thì ai có anon key đều gọi được `/rest/v1/rpc/tkauto_activate` trực tiếp, **bỏ
qua toàn bộ phần kiểm chữ ký thiết bị trong Edge Function**. Đó là chỗ dễ mất
nhất trong cả thiết kế.

---

## 8. Những gì đường này **không** có

⛔ **Không có auto-update, không có update bắt buộc.** Function `releases` luôn
trả `update_available: false`, và đó là câu trả lời **đúng**: không có bản phát
hành nào được ghi nhận trên Supabase, vì phát hành cần manifest ký bằng khoá
release — khoá không được đưa lên mây. Hệ quả: gửi bản backend mới bằng tay, và
cơ chế `mandatory` không có đường nào để bật.

Function đó vẫn **phải** tồn tại: `SecureBootstrap.tsx:62` gọi
`await invoke('check_backend_update')` **không có try/catch**, ngay trước
`launch_backend`. Thiếu nó thì endpoint trả 404, invoke ném lỗi, phase thành
`error`, và **backend không bao giờ chạy** — dù kích hoạt đã thành công.

⛔ **Không có endpoint admin trên Internet.** Tạo và thu hồi key đi thẳng vào
Postgres bằng mật khẩu database. Một endpoint tạo key công khai là thứ nguy hiểm
nhất có thể dựng.

⛔ **Không có `/v1/artifacts/{id}/download`.** Đi cùng mục auto-update ở trên.

Muốn đủ cả ba thì phải dùng control plane Python (đã có sẵn cả ba endpoint) sau
một tunnel — `scripts/run_control_plane_dev.ps1` và
`deploy/control-plane/`. Supabase vẫn dùng được làm database cho nó: đặt
`TKAUTO_CONTROL_DATABASE_URL` thành connection string ở mục 2.

---

## 9. Đã kiểm được gì, và chưa kiểm được gì

**Đã kiểm, tự động, chạy cùng `pytest`:**

| Bài test | Kiểm gì |
|---|---|
| `backend/tests/test_supabase_lease_parity.py` | Bản TypeScript ký ra token **y từng byte** như `LeaseSigner` của Python, và token đó qua được `LeaseVerifier`. Cùng digest key, device_id, bằng chứng thiết bị, so sánh semver, thứ tự `features`. |
| `backend/tests/test_supabase_edge_functions.py` | Chạy **chính ba handler** bằng Node với bằng chứng thiết bị thật; chữ ký giả → 401 và **không chạm Postgres**; từng mã phán xét → đúng mã HTTP; cấu hình yếu → function chết chứ không chạy yếu; DB sự cố → 503 chứ không 403. |
| `backend/tests/test_supabase_sql_is_parseable.py` | Mọi câu lệnh **và mọi thân plpgsql** qua được parser của chính PostgreSQL (libpg_query); không bảng nào rơi vào `public`. |

**Đã kiểm trên database thật**, ngày 13/10/2026, project
`ipuszafddmavvxjqvhsj` (ap-southeast-2), Postgres 17.11 — 26 phép đo, gọi thẳng
hàm plpgsql qua psycopg rồi dọn sạch dữ liệu thử:

| Đo gì | Kết quả |
|---|---|
| `anon` và `authenticated` gọi `tkauto_activate` | **không gọi được** |
| `service_role` gọi | được |
| Bảng key nằm ở `public` | không có cái nào |
| RLS trên mọi bảng, số policy | bật, 0 policy |
| Chống phát lại nonce | chặn |
| Cùng máy, nonce mới | cho qua |
| Vượt `max_devices = 1` | chặn (`device_limit`) |
| Cùng `device_id`, khoá công khai khác | chặn (`device_conflict`) |
| Thu hồi thiết bị rồi kích hoạt lại | chặn (`device_revoked`) |
| Digest key sai | chặn (`license_invalid`) |
| Thu hồi license | chặn |
| Chống dò tần số 10/phút, gọi 12 lần | 10 qua, 2 chặn |
| View rút `group.` từ `features` | đúng |

⛔ **Phép đo đầu bảng là phép quan trọng nhất.** Mặc định Postgres cho PUBLIC
gọi mọi hàm mới, và `public` là schema PostgREST phục vụ — nếu `anon` gọi được
`tkauto_activate` thì ai có file `.exe` cũng gọi `/rest/v1/rpc/tkauto_activate`
trực tiếp và **bỏ qua toàn bộ phần kiểm chữ ký thiết bị**. Đo được `false` mới
là bằng chứng; đọc code chỉ là ý định.

**Đã deploy và đo cả chuỗi thật**, cùng ngày, trên
`https://ipuszafddmavvxjqvhsj.supabase.co/functions/v1` — 24 phép, gọi HTTP
thật với bằng chứng thiết bị Ed25519 ký thật, lease xác thực bằng bộ khoá công
khai trong `desktop-public-keys.json`:

| Đo gì | Kết quả |
|---|---|
| `POST /activate` | 200, lease verify được bằng khoá công khai thật |
| `features` trong lease | `app.start, accounts.manage, upload.video, group.hn01, role.owner` |
| `group_id` / `role` đọc lại | `hn01` / `owner` |
| `renew_after < expires_at` | đúng |
| `Cache-Control` | `no-store` |
| Không gửi `Authorization` vẫn vào được | đúng → `verify_jwt` đã tắt |
| Phát lại cùng nonce | 409 |
| Máy thứ hai, `max_devices = 1` | 403 |
| Key sai / chữ ký giả | 401 / 401 |
| Trường gõ sai tên | 422 |
| Client dưới sàn phiên bản | 426 |
| `POST /lease/renew` | 200, `jti` khác lease trước |
| Kẻ khác ký, khai `device_id` người khác | 401 |
| `POST /releases/check` | 200, `{"update_available": false}` |
| `/lease/<đường lạ>` | 404 |

Và 14 phép từ **góc nhìn kẻ tấn công có anon key** (tức bất kỳ ai có file
`.exe`) — thử với cả `anon` JWT và `publishable key`:

| Thử gì | Kết quả |
|---|---|
| `GET /rest/v1/licenses`, `devices`, `used_nonces`, `audit_events`, `overview` | **404** — không tồn tại từ góc nhìn API |
| `POST /rest/v1/rpc/tkauto_activate` | **401** |
| `POST /rest/v1/rpc/tkauto_renew_device` | **401** |

⛔ **404 chứ không phải 403, và đó là điều muốn.** Bảng nằm ở schema `license`
nên PostgREST không có đường nào tới nó — không phải "bị từ chối" mà là "không
tồn tại". Một 403 còn tiết lộ là có bảng tên đó.

**Còn lại một phép phải đo bằng tay:** build bản desktop với
`TKAUTO_CONTROL_PLANE_URL` ở trên rồi kích hoạt một key → app phải lên tới
`ready`. Phần HTTP đã đo xong ở trên; phép này đo thêm đoạn launcher ghi lease
xuống đĩa và `check_backend_update` → `launch_backend` chạy tiếp.
