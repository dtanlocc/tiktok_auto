# Hướng dẫn sử dụng, từ đầu đến cuối

Viết 06/10/2026. Định dạng dữ liệu và hành vi trong tài liệu này **đọc ra từ
code**, không phải nhớ lại: mỗi chỗ quan trọng có kèm file để kiểm lại.

⛔ **Điều phải nói trước:** tôi mô tả các luồng dựa trên **API và code**, không
phải từ việc tự bấm hết giao diện. Luồng tôi đã chạy thật đầu-cuối là **đăng
nhập** (qua `/bulk-login`). Những luồng khác tôi nêu đúng endpoint và đúng định
dạng, nhưng vị trí nút trên giao diện thì anh là người biết rõ hơn tôi.

---

## 0. Chọn một trong hai cách dùng

| | Bản `.exe` (gói friends) | Chạy từ source |
|---|---|---|
| Dùng khi | chỉ cần **chạy**, kể cả máy không có Internet | cần sửa code, xem log, build |
| Cài | copy thư mục, bấm `CHAY.bat` | `git clone` + `setup.ps1` |
| Dữ liệu | `%LOCALAPPDATA%\com.tiktokauto.desktop\database.db` | `backend\database.db` |
| `bios.txt` | cạnh hai file `.exe` | `backend\bios.txt` |
| Key license | **không cần** | không cần |

Bản dùng được mới nhất: `release\friends\0.1.11\`. Chi tiết gói và cách dựng ở
mục 9.

---

## 1. Cài từ source

```powershell
git clone --recurse-submodules -b feat/invisible-playwright-0.25.4 https://github.com/dtanlocc/tiktok_auto.git
cd tiktok_auto
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Cần sẵn **uv**, Git, Node 20+. Không cần tự cài Python. Ba cái bẫy và cách tránh
nằm trong [README.md](../README.md) — đọc mục "Bốn chỗ dễ sai trên máy mới"
trước khi loay hoay, nhất là chuyện Windows mặc định **không cho chạy `.ps1`**
và chuyện clone vào đường dẫn sâu làm hụt file mà vẫn báo thành công.

## 2. Chạy

```powershell
uv run uvicorn --app-dir backend app.main:app --port 9000
```

```powershell
npm --prefix frontend run dev
```

Backend ở `127.0.0.1:9000`, giao diện ở `127.0.0.1:1420`. Bản desktop thật:
`npm --prefix frontend run desktop:dev`.

Lần đầu khởi động, backend tự tạo `backend\database.db`, tự chạy migration
(`alembic`) và tự tải engine trình duyệt nếu chưa có — **549 MB engine + 116 MB
geoip**, cache ở `%LOCALAPPDATA%\invisible-playwright\`, chỉ tải một lần cho cả
máy.

---

## 3. Nạp proxy TRƯỚC, account sau

Thứ tự này không phải chuyện thẩm mỹ: import account có bước tự gán proxy, nên
không có proxy thì phải gán lại bằng tay sau.

**Định dạng proxy** (`proxies_router.py:_parse_proxy_line`) — nhận cả hai kiểu:

```
host|port|protocol|username|password        (tối thiểu 3 trường)
protocol://host:port
protocol://host:port:username:password
protocol://username:password@host:port
```

Dán vào màn hình proxy, hoặc gọi `POST /api/v1/proxies/import-text`. Có cả
`import-file`.

Sau khi nạp, chạy **kiểm tra proxy** (`POST /api/v1/proxies/check`): nó ghi lại
`check_status`, `exit_ip`, `country`, `latency_ms`, `tiktok_ok`, `cdn_ok`. Proxy
bị tắt (`enabled=false`) sẽ không được cấp cho account nào.

⛔ **Một proxy chỉ chạy một account một lúc.** Đây là giới hạn đã đo, không phải
lựa chọn: nhiều phiên qua cùng một IP thì TikTok và CDN bắt đầu từ chối. Xem
`proxy_max_concurrent` ở mục 8.

## 4. Nạp account

**Định dạng dán** (`accounts_router.py:import-raw`), ngăn bằng dấu `|`:

```
username|password|email|email_password|refresh_token|client_id|cookies
```

- **Tối thiểu 6 trường.** Trường thứ 7 (cookies) **tuỳ chọn** — nick định đăng
  nhập bằng mật khẩu + OTP thì không cần.
- `cookies` nhận **cả hai dạng**: mảng JSON kiểu Playwright, hoặc chuỗi
  `a=b; c=d`.
- `refresh_token` + `client_id` là của **Microsoft Graph**, để tự đọc mã OTP
  trong hộp thư. Thiếu hai trường này thì đường credential sẽ dừng ở bước chờ mã.
- **Không tạo trùng**: nếu email (rồi mới đến username) đã có, bản ghi cũ được
  **cập nhật** chứ không sinh thêm dòng mới.
- Không đặt `batch_tag` thì nó tự thành `LÔ_YYYYMMDD`.

Có thêm `import-file`, và `POST /accounts/auto-allocate-proxies` để rải proxy
cho các account chưa có.

⛔ **"Nhóm" trong app là `batch_tag`, không phải nhóm license.** Nút chuyển cụm
gọi `/accounts/move-to-group` và nó chỉ đổi `batch_tag` — một cách sắp xếp để
nhìn. Nhóm theo license (`group_id`, mục 10) là chuyện khác hẳn và không liên
quan. Hai chữ "nhóm" này rất dễ lẫn.

---

## 5. Chọn đường ra Internet

`POST /api/v1/tasks/proxy-mode` với `use_proxy`:

- `true` — mỗi account đi qua proxy được gán cho nó.
- `false` — đi bằng mạng thật của máy (kể cả VPN đang bật).

⛔ **VPN đổi IP theo từng kết nối thì TikTok giết phiên giữa lúc đang chạy.** Đã
đo. Nếu dùng mạng thật thì phải là đường ra **IP cố định**.

## 6. Đăng nhập

Hai đường, cùng một API `POST /api/v1/tasks/bulk-login`:

| `login_method` | Làm gì |
|---|---|
| `COOKIE` | thử cookie đã lưu. Nhanh, không tốn lượt, không cần OTP |
| `CREDENTIAL` | gõ username + mật khẩu, rồi đọc mã OTP từ hộp thư |

Thực tế cả hai đều chạy `CookieThenCredentialLoginStrategy`: **cookie trước**,
chỉ rơi sang credential khi cookie chết. Nên nick còn cookie tốt sẽ không bao
giờ chạm tới đường mật khẩu.

⛔ **Đăng nhập gõ `username`, không gõ email** (từ 24/09/2026). Email chỉ là
đường thử thứ hai khi TikTok trả lời "Account doesn't exist".

**Để xem tận mắt**: `POST /api/v1/tasks/debug-login` với một `account_id` mở một
cửa sổ **hiện lên**, tự đăng nhập rồi **giữ nguyên** cho anh thao tác tay. Đóng
cửa sổ hoặc gọi `/debug-login/stop` để kết thúc. Mỗi account một phiên debug.

**Verdict khi thất bại** đã được phân biệt rõ, đừng đọc chung thành "lỗi": sai
mật khẩu, hết lượt thử (`Maximum`), cần xác minh 2 bước, và lỗi máy chủ tạm thời
là bốn chuyện khác nhau với bốn cách xử lý khác nhau.

## 7. Các việc chạy hàng loạt

| Việc | API | Ghi chú |
|---|---|---|
| Kiểm tra sức khoẻ nick | `POST /tasks/quick-health-check` | có bản chạy liên tục: `/start-continuous`, `/stop-continuous` |
| Đổi hồ sơ (avatar, bio, username) | `POST /tasks/bulk-update-profile` | bio lấy ngẫu nhiên từ `bios.txt`, mỗi dòng một bio, **emoji dùng được** |
| Đăng video | `POST /tasks/bulk-upload-video` | xem mục 7.1 |
| Đăng ảnh/media | `POST /tasks/bulk-upload-media` | |
| Hẹn giờ đăng | `POST /tasks/schedule-upload-video`, `/schedule-upload-media` | xem và xoá qua `/scheduled-uploads` |
| Đồng bộ thống kê | `POST /tasks/sync-analytics` | ghi follower, view, like… vào DB |

⛔ **Hẹn giờ là của app, không phải của TikTok.** Nút "Lên lịch" trên TikTok bị
khoá với nick bot, nên lịch do app giữ và app tự đăng đúng giờ.

### 7.1 Video: thư viện trước, rồi lô

1. `POST /tasks/video-library/scan` (hoặc `pick-folder` / `pick-files`) để app
   biết có những video nào.
2. `POST /tasks/video-batches` tạo một **lô** từ các video đó.
3. `POST /tasks/bulk-upload-video` chạy lô cho các account đã chọn.

Upload đi qua **hộp thoại file thật của Windows**, không phải gán ngầm vào ô
input — đó là cách một người làm, và là cách mọi phép đo trong dự án này được
lấy. Nó cần cửa sổ trình duyệt trên **desktop riêng** mà engine tạo ra; chuyện
này đã xử lý trong code, nêu ra để anh biết nếu thấy log nói về desktop.

⛔ **Studio Posts không tự cập nhật.** Thấy video chưa xuất hiện thì **reload
trước** rồi hãy kết luận là bị nuốt. Đã có một lần kết luận sai vì chuyện này.

## 8. Điều khiển trong lúc chạy

| | API |
|---|---|
| Bắt đầu / dừng toàn bộ | `POST /tasks/start-global`, `/stop-global` |
| Tạm dừng / tiếp tục toàn bộ | `/pause-global`, `/resume-global` |
| Tạm dừng một account | `/pause-account/{id}`, `/resume-account/{id}` |
| Số phiên song song | `POST /tasks/proxy-concurrency` |
| Trạng thái hàng đợi | `GET /tasks/status` |

`GET /tasks/status` trả về `active_count`, `queued_count`, `proxy_max_concurrent`,
`machine_max_tabs` — đây là chỗ xem app đang thật sự làm gì.

Màn hình **LiveScreens** nhận ảnh màn hình qua WebSocket `/ws/screens`. Nó chỉ
để **xem**: không gửi được chuột/bàn phím vào trang. Đây là quyết định có chủ ý,
lý do nằm trong [REMOTE_CONTROL_FUTURE.md](REMOTE_CONTROL_FUTURE.md).

**TerminalConsole** là chỗ đọc log nghiệp vụ theo thời gian thực — nhìn đây
trước khi đoán.

---

## 9. Đóng gói `.exe`

```powershell
.\scripts\build_friends_release.ps1 -Version 0.1.12
.\scripts\stage_friends_portable.ps1 -Version 0.1.12
```

Script đầu cho ra hai file `.exe` (backend đóng gói Nuitka + app desktop Tauri),
đã chạy smoke test thật trên bản đóng gói. Script thứ hai biến thư mục đó thành
**tự chứa ~0,7 GB**: nhúng engine vào `engine\`, đặt sẵn `bios.txt`, viết
`CHAY.bat`. Máy đích **không cần Internet, không cần cài gì** — copy thư mục,
bấm `CHAY.bat`.

Thêm `-IncludeDatabase` nếu muốn mang account theo. Mặc định **không** mang, vì
`database.db` chứa mật khẩu và cookie của mọi nick.

⛔ **Build sẽ dừng ở cổng bảo mật trước bước đóng ZIP** cho tới khi khoá
OmoCaptcha được đổi (mục 11). Hai `.exe` và thư mục thì đã dùng được; ZIP chỉ là
lớp đóng gói.

## 10. Database: local, nhóm, sao lưu

Mặc định mỗi bản cài giữ DB riêng (`DATABASE_URL`, SQLite). Cột `group_id` đã có
trên `accounts`, `proxies`, `tiktok_video_metrics`: **NULL = bản cài riêng**.
License có token `group.<id>` thì mới thuộc một nhóm và chỉ thấy dữ liệu nhóm
đó. Thiết kế đầy đủ trong
[KEY-NHOM-DATABASE-CLOUD.md](KEY-NHOM-DATABASE-CLOUD.md).

⛔ **Sao lưu `database.db` bằng `cp` là mất dữ liệu, mà không báo lỗi.** DB chạy
chế độ WAL nên phần vừa ghi còn nằm trong `database.db-wal`. Cách đúng, chạy
được cả khi app đang mở:

```bash
python -c "import sqlite3; c=sqlite3.connect('backend/database.db'); c.execute('VACUUM INTO ?', ('backup.db',))"
```

Schema do **alembic** nắm. Đổi model thì:

```powershell
cd backend
uv run alembic revision --autogenerate -m "mo ta ngan"
```

rồi **đọc lại file sinh ra** trước khi tin nó — autogenerate hay đề xuất kèm cả
những thứ không thuộc việc của mình. App tự `upgrade head` lúc khởi động.

## 11. Key license

Chưa chạy bao giờ: `LICENSE_*` đang để trống nên hiện **không có rào nào**, và
bản friends cố ý không cần key.

Khi muốn bật:

| Việc | Ở đâu |
|---|---|
| Service | `control_plane/` (+ `Dockerfile`) |
| Triển khai | `deploy/control-plane/` (compose + Caddy) |
| Sinh khoá Ed25519 | `scripts/generate_control_plane_keys.py` |
| Thư mục bí mật của operator | `scripts/initialize_operator_environment.py` |
| **Tạo / thu hồi / xem key** | `scripts/control_plane_admin.py` |
| Build bản có license | `scripts/build_backend_release.ps1` |

`control_plane_admin.py` có: `health`, `create-license`, `list-licenses`,
`update-license`, `list-devices`, `register-release`. Nhóm và quyền đi vào
`--features` dạng `group.acme,role.owner`; `--max-devices` cho nhiều máy dùng
một key.

⛔ **Khoá OmoCaptcha đang bị lộ.** Nó từng nằm cứng trong `config.py` từ
10/07/2026 đến 15/09/2026, và các commit đó **nằm trên `origin/main` của một repo
public** — tức bất kỳ ai cũng đọc được bằng một lệnh `git show`. Khoá đó **vẫn
đang dùng** trong `backend/.env`. Xoá khỏi HEAD không có tác dụng gì với lịch sử
đã push: **phải đổi khoá ở omocaptcha.com**. Đây cũng là thứ đang chặn bước đóng
ZIP ở mục 9.

## 12. Những cái bẫy đã biết

- **Mở trình duyệt thỉnh thoảng treo** trên RDP (~50%). Có fail-fast 25s rồi mở
  lại, nên đừng kết luận nick lỗi khi thấy một lần treo.
- **Pool proxy thật ra chỉ là hai IP.** "CDN từ chối" thường là vượt giới hạn
  kết nối trên một IP, **không phải** cookie chết.
- **OTP có thể về một hộp thư khác** với email ghi trong app. Form đăng nhập
  cũng tự dựng lại và **xoá chữ vừa gõ** — code đã xử lý, nêu ra để anh đọc log
  không hoảng.
- **`Save` trả 200 không có nghĩa là đã lưu** ở luồng đổi hồ sơ; phải đợi hộp
  thoại đóng.
- **Camoufox không dùng được trên máy này** và đã bị gỡ khỏi dự án: bản 152 mở
  được cửa sổ nhưng không mở nổi một trang (GPU process hỏng trên RDP).
