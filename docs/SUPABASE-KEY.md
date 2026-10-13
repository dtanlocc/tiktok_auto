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
| Manifest bản phát hành **đã ký** | bảng `license.releases` | Có — chỉ token, không phải khoá |
| Bí mật vé tải | `<operator>/secrets/download-grant-secret` **và** secret Edge Function | Có |
| File backend `.exe` | **GitHub Release** trên một repo **private** | Không — ở GitHub, xem mục 8 |
| PAT đọc repo artifact | `<operator>/secrets/github-artifacts.json` **và** secret Edge Function | Có |
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

Script đọc pepper, `kid`, khoá lease **và bí mật vé tải** từ thư mục operator
rồi ghi `<operator>/supabase-functions.env`. Nó không in giá trị nào ra màn hình, và
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

## 5. Deploy bốn function

```bash
tools/supabase-cli/node_modules/.bin/supabase functions deploy activate lease releases artifacts
```

`supabase/config.toml` đã đặt `verify_jwt = false` cho cả bốn. Đó là **bắt buộc,
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

⛔ **Thu hồi có hiệu lực ở lần gia hạn kế tiếp, không phải ngay** — và "kế
tiếp" là bao lâu thì tuỳ tình huống. Đo ngày 13/10/2026 trên máy thật:

| Tình huống | Backend dừng và `.exe` bị xoá sau |
|---|---|
| App đang mở, có mạng | **≤ 15 phút** (đợt gia hạn kế tiếp) |
| Khách mở lại app | **ngay lập tức** |
| Máy đang offline | khi lease hết hạn (**≤ 1 giờ**) + ≤ 60 giây |
| Key hết hạn theo `--expires-days` | **≤ 60 giây** |

Ba dòng đầu khác nhau vì **hai cơ chế khác nhau**. Một 403 ở `/lease/renew` làm
`invalidate_local_entitlement` chạy **ngay** — dừng backend, xoá `.exe`, xoá
lease; giao diện gọi gia hạn mỗi 15 phút (`SecureBootstrap.tsx:87`) và một lần
nữa lúc mở app. Máy offline không nhận được 403 nào, nên chỉ còn đường lease tự
hết hạn rồi watchdog 60 giây đọc ra. Đó là **cố ý**: mất mạng không được phép
trông giống như bị thu hồi.

Muốn nhanh hơn cho trường hợp offline thì hạ `TKAUTO_LEASE_TTL_SECONDS` (sàn
300 giây), và trả giá bằng số lần gọi gia hạn — và bằng việc khách phải online
thường xuyên hơn.

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

## 8. Key hết hạn thì code biến mất — và tải lại khi có key

Đây là thứ launcher **đã** làm sẵn, không phải tính năng mới:

- [lib.rs:488](../frontend/src-tauri/src/lib.rs:488) `spawn_entitlement_monitor`
  chạy **mỗi 60 giây**: lease không còn hợp lệ → dừng backend → **xoá
  `backend-*.exe` và `backend-current.json`** khỏi `%LOCALAPPDATA%`.
- Server trả 403/426 (thu hồi / dưới sàn phiên bản) →
  `invalidate_local_entitlement` → xoá thêm cả file lease.
- [lib.rs:1055](../frontend/src-tauri/src/lib.rs:1055) `launch_backend` **không
  có đường dự phòng**: không có `.exe` là không chạy.
- Tải lại tự động: sau khi xoá, `current_version` thành `"0.0.0"` nên bản nào
  cũng "mới hơn" → tải về, đối chiếu sha256 + kích thước với **manifest đã ký**
  rồi mới chạy.

⛔ **Phải ship bản licensed, không phải bản friends.** Bản friends nhúng backend
vào installer (`trusted_friends_backend`) và bỏ qua hẳn phần kiểm update — khách
đã có code trong tay, không có gì để xoá. `build_licensed_unsigned_release.ps1`
đặt `TKAUTO_FRIEND_BUILD = $null` nên nó **là** bản licensed.

⛔ **Và phải có một bản phát hành đang bật, nếu không bản licensed KHÔNG CHẠY
NỔI LẦN ĐẦU.** `check_backend_update` là đường **duy nhất** lấy backend binary
về. Nếu `releases/check` trả `update_available: false` khi máy chưa có backend
thì `launch_backend` chết với *"Backend binary chưa được cài."* — không phải sau
khi hết hạn, mà ngay lần chạy đầu. Kiểm bằng:

```bash
uv run python scripts/supabase_release_admin.py list --operator-directory D:/tkauto-operator-dev
```

### Phát hành một bản

⛔ **File backend nằm trên Cloudflare R2, không phải Supabase Storage.** Đo
ngày 13/10/2026: Free plan của Supabase **chặn upload ở 50 MB và không nâng
được từ dashboard**, trong khi backend 0.1.12 là **50,7 MiB** — vượt đúng 0,7
MiB. R2 cho 10 GB lưu, **không tính egress**, một PUT tới 5 GB. Đổi được chỗ
chứa mà **không phải ký lại manifest nào** là vì `download_url` trỏ vào function
`artifacts` chứ không trỏ thẳng vào chỗ chứa — lớp gián tiếp đặt từ đầu, giờ nó
trả công.

#### Chỗ chứa file: ba lần đổi, và vì sao

⛔ **Supabase Storage — loại.** Free plan chặn upload ở **50 MB** và
[không nâng được từ dashboard](https://supabase.com/docs/guides/troubleshooting/upload-file-size-restrictions-Y4wQLT);
backend 0.1.12 là **50,7 MiB**, vượt đúng 0,7 MiB.

⛔ **Cloudflare R2 — loại.** Miễn phí 10 GB và không tính egress, nhưng **đòi
thêm phương thức thanh toán** để bật lần đầu (đo ngày 13/10/2026 — tài liệu
chính chủ của Cloudflare không nói gì về việc này).

✅ **GitHub Release trên một repo private.** Asset tới 2 GB, không thẻ, và tài
khoản GitHub thì đã có.

Đổi được chỗ chứa **ba lần mà không phải ký lại một manifest nào** là vì
`download_url` trỏ vào function `artifacts` chứ không trỏ thẳng vào chỗ chứa.
Lớp gián tiếp đó đặt từ đầu để signed URL ngắn hạn không làm manifest chết — nó
đã trả công ba lần.

#### Dựng kho artifact lần đầu, từng bước

**1. Tạo một repo PRIVATE riêng**

- <https://github.com/new> → tên ví dụ `tkauto-artifacts` → chọn **Private** →
  Create.

⛔ **Không dùng repo `tiktok_auto`.** Repo đó đang **public**, và asset của
release trên repo public tải được **không cần xác thực gì** — lúc đó toàn bộ
chuỗi vé tải, kiểm thiết bị, kiểm license trở thành trang trí.
`supabase_release_admin.py` **từ chối chạy** nếu repo không private.

**2. Tạo fine-grained PAT**

- <https://github.com/settings/personal-access-tokens/new>
- **Repository access** → *Only select repositories* → chọn đúng repo vừa tạo.
- **Permissions → Repository permissions → Contents: Read and write.** ⛔ Chỉ
  một quyền này; release asset nằm dưới Contents.
- Expiration: tuỳ anh. Hết hạn thì phát hành báo 404 và phải tạo token mới.
- Generate → copy token (`github_pat_...`). ⛔ **Chỉ hiện một lần.**

**3. Ghi vào thư mục operator**

`<operator>/secrets/github-artifacts.json`:

```json
{"repo": "dtanlocc/tkauto-artifacts", "token": "github_pat_..."}
```

⛔ **File này nằm ngoài repo**, như mọi bí mật operator khác.

**4. Kiểm trước khi phát hành**

```bash
uv run python scripts/supabase_release_admin.py check-storage --operator-directory D:/tkauto-operator-dev
```

| Nhận được | Nghĩa là |
|---|---|
| `GitHub OK: ... là repo PRIVATE và token đọc/ghi được` | xong |
| `Repo ... đang PUBLIC` | đổi repo thành private, hoặc dùng repo khác |
| `Không thấy repo ...` | sai tên repo **hoặc** token không có quyền đọc nó — với repo private GitHub trả 404 cho cả hai, cố ý, để không tiết lộ repo có tồn tại |

**5. Đẩy thông tin lên Edge Function**

```bash
uv run python scripts/supabase_make_function_env.py --operator-directory D:/tkauto-operator-dev
```

```bash
tools/supabase-cli/node_modules/.bin/supabase secrets set --env-file D:/tkauto-operator-dev/supabase-functions.env
```

Script đọc **cùng một file** `github-artifacts.json` mà bước phát hành đọc, nên
hai bên không thể trỏ vào hai repo khác nhau.

Rồi mỗi lần có bản mới:

```bash
uv run python scripts/supabase_release_admin.py publish --operator-directory D:/tkauto-operator-dev --file release/backend/0.1.12/backend-0.1.12.exe --version 0.1.12 --minimum-launcher-version 0.1.0
```

Script kiểm đầu PE (`MZ`), tính sha256, **ký manifest ngay tại máy này** bằng
`keys/release-private.pem`, tự xác thực lại bằng khoá công khai, kiểm repo đúng
là **private**, tạo release `backend-<version>` rồi tải asset lên, và ghi một
dòng vào `license.releases`.

⛔ **`storage_object` giữ asset id, không giữ tên file.** Với GitHub thì tên file
không đủ để tải — phải có asset id. Cột đó là một định vị không trong suốt mà
chỉ function `artifacts` biết cách đọc; nhờ vậy đổi chỗ chứa không phải đổi
schema.

⛔ **Khoá ký release không bao giờ rời máy.** Chỉ **chuỗi token đã ký** được lưu
lên Supabase; Edge Function phát lại chuỗi đó chứ không ký gì. Chiếm được
Supabase thì cấp được lease (dùng lậu app) nhưng **không ký được manifest** —
tức không gửi được bản cài độc cho khách. Đây là ranh giới quan trọng nhất trong
cả hệ thống.

⛔ **`download_url` trong manifest phải cố định**, vì nó nằm trong phần đã ký.
Nên nó trỏ vào function `artifacts`
(`/functions/v1/artifacts/<artifact_id>/download`), **không** trỏ thẳng vào
signed URL của Storage — signed URL hết hạn sau vài phút và sẽ làm manifest đã ký
thành vô dụng. Đổi tên function `artifacts` là mọi manifest trong DB chết theo.

Ngừng phát một bản (file vẫn còn trong Storage, máy đang tải dở không bị đứt):

```bash
uv run python scripts/supabase_release_admin.py deactivate --operator-directory D:/tkauto-operator-dev --artifact-id artifact_...
```

### Nói thẳng về chống dịch ngược

Cơ chế này **nâng rào**, không phải khoá cửa:

- Ai đã từng có key hợp lệ thì **copy được** `backend-*.exe` trong lúc còn hạn.
  Xoá bản local không lấy lại được bản họ đã giữ.
- Nuitka biên dịch khó đọc hơn `.py` nhiều, nhưng **không phải hộp đen** —
  chuỗi, cấu trúc module, tên hàm vẫn moi ra được.

Giá trị thật: **người hết hạn không dùng tiếp được**, và **người chưa từng trả
tiền không có code**. Không phải: "người đã từng trả tiền không đọc được code".
Muốn mức sau thì phần lõi phải chạy trên server chứ không gửi xuống máy khách —
việc khác hẳn.

Và một hệ quả cố ý phải biết trước: khách **offline quá 1 giờ** (TTL lease mặc
định) sẽ bị xoá `.exe` và cần mạng để tải lại.

---

## 9. Những gì đường này vẫn **không** có

⛔ **Không có endpoint admin trên Internet.** Tạo/thu hồi key và phát hành đi
thẳng vào Postgres bằng mật khẩu database. Một endpoint tạo key công khai là thứ
nguy hiểm nhất có thể dựng.

⛔ **`mandatory` được lưu nhưng chưa dùng tới.** Cột có, manifest ký kèm, nhưng
chưa có đường nào buộc khách cập nhật ngay.

⛔ **Băng thông GitHub Release không có hạn mức công bố**, và byte đi thẳng từ
CDN của GitHub về máy khách — **không** qua Edge Function. Đó là lý do function
`artifacts` gọi GitHub với `redirect: "manual"` rồi chuyển hướng, chứ không tự
truyền byte: tự truyền thì tốn băng thông của project Supabase **và** chạm trần
150 giây wall-clock nếu máy khách tải chậm (51 MB ở 300 KB/s là ~170 giây).

---

## 10. Đã kiểm được gì, và chưa kiểm được gì

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

Và **14 phép cho cả chuỗi phát hành**, với một bản backend thật 41,4 MB đã
publish lên Storage:

| Đo gì | Kết quả |
|---|---|
| `releases/check` khi máy chưa có backend | `update_available: true` |
| Manifest verify bằng khoá **release** thật | đúng kid |
| `download_url` trỏ vào function `artifacts` | đúng |
| `GET download_url` kèm vé | **302** sang Storage signed URL |
| Tải thật từ Storage | 200, **41,4 MB** |
| Kích thước và **sha256** so với manifest đã ký | **khớp** |
| Dùng lại cùng một vé | **401** (vé một lần) |
| Đã là bản mới nhất | `update_available: false` |

⛔ **Phép sha256 là phép quan trọng nhất ở đây.** Nó chứng minh file đi từ máy
operator, qua Storage, về tới client mà không đổi một byte — và client kiểm nó
bằng **chữ ký của khoá release**, khoá không nằm trên Supabase. Đó là thứ ngăn
một Supabase bị chiếm gửi bản cài độc cho khách.

**Còn lại một phép phải đo bằng tay:** build bản desktop với
`TKAUTO_CONTROL_PLANE_URL` ở trên rồi kích hoạt một key → app phải lên tới
`ready`. Phần HTTP đã đo xong ở trên; phép này đo thêm đoạn launcher ghi lease
xuống đĩa và `check_backend_update` → `launch_backend` chạy tiếp.
