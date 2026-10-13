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

Rồi lấy connection string: **Project Settings → Database → Connection string →
URI**, chọn **Session pooler** (cổng 5432, không phải 6543), và dán vào:

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
cp supabase/.env.example supabase/.env
```

Điền vào `supabase/.env` (file này đã bị gitignore). Lấy khoá lease dạng một
dòng:

```bash
uv run python -c "import re,sys;print(re.sub(r'-----[A-Z ]+-----|\s+','',open(sys.argv[1]).read()))" D:/tkauto-operator-dev/keys/lease-private.pem
```

Rồi:

```bash
tools/supabase-cli/node_modules/.bin/supabase secrets set --env-file supabase/.env
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

**Chưa kiểm được — phải anh kiểm bằng một lần kích hoạt thật:**

Máy phát triển này không có Postgres (Docker Desktop có cài nhưng service
`com.docker.service` đang Stopped, bật nó cần quyền admin), nên `supabase start`
không chạy được. Hệ quả: **toàn bộ hành vi trong plpgsql chưa từng chạy** — khoá
hàng license, đếm thiết bị so với `max_devices`, chống phát lại nonce, chống dò
tần số. Cú pháp đúng, hành vi chưa đo.

Sau khi deploy, đo bốn thứ này:

1. **Kích hoạt một key** → app lên được tới `ready`.
2. **Kích hoạt máy thứ hai** với `--max-devices 1` → phải bị từ chối (HTTP 403).
3. **Thu hồi thiết bị rồi kích hoạt lại cùng máy đó** → phải bị từ chối. (Đúng
   chỗ này bản Python từng có lỗ: `activate` không đọc `revoked_at`, chỉ `renew`
   đọc — đã sửa ngày 13/10/2026 kèm test hồi quy.)
4. **Gọi `/activate` sai key hơn 10 lần trong một phút** → phải nhận 429.
