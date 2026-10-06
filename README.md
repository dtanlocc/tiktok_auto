# tiktok_auto

📖 **Dùng app thì đọc [docs/HUONG-DAN-SU-DUNG.md](docs/HUONG-DAN-SU-DUNG.md)** —
hướng dẫn từ đầu đến cuối: nạp proxy và account, đăng nhập, đổi hồ sơ, đăng
video, đóng gói `.exe`, quản lí key. File này chỉ nói về **cài đặt và dựng**.

Backend FastAPI + frontend React/Tauri, điều khiển trình duyệt qua fork
[`invisible_playwright`](https://github.com/dtanlocc/invisible_playwright-custom)
được gắn vào repo này dưới dạng **git submodule**.

## Cài trên máy mới

Cần có sẵn: [uv](https://docs.astral.sh/uv/), Git, và Node 20+ (chỉ để chạy
frontend). Không cần tự cài Python — `uv` tải đúng bản ghi trong
`.python-version`.

```powershell
git clone --recurse-submodules -b feat/invisible-playwright-0.25.4 https://github.com/dtanlocc/tiktok_auto.git
cd tiktok_auto
.\scripts\setup.ps1
```

⛔ **Windows mặc định không cho chạy file `.ps1`.** Dòng thứ ba ở trên sẽ báo
*"running scripts is disabled on this system"* trên một máy chưa đổi gì —
`Get-ExecutionPolicy -List` cho thấy `CurrentUser` và `LocalMachine` là
`Undefined`, và mặc định của Windows client là `Restricted`. Gọi script qua
tiến trình riêng để không phải đổi thiết lập nào của máy:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Hoặc đổi một lần cho tài khoản của mình, nếu máy đó là máy làm việc thường
xuyên: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. File do `git
clone` tạo ra không mang dấu "tải từ Internet", nên `RemoteSigned` chấp nhận nó.

Và cái chặn đó **không chỉ ăn script trong repo này**: trong PowerShell, `npm`
phân giải thành `C:\Program Files\nodejs\npm.ps1`, nên `Restricted` chặn mọi
lệnh npm — kể cả `npm run dev` về sau. Chạy script qua tiến trình `Bypass` thì
npm bên trong nó cũng thoát theo; gọi npm trực tiếp từ shell thường thì không,
và lúc đó dùng `npm.cmd` (shim `.cmd` không chịu chính sách này) hoặc đổi
`Set-ExecutionPolicy` như trên.

⛔ **`-b` không bỏ được cho tới khi nhánh đó về `main`.** Bỏ nó ra thì clone rơi
vào `main`, và `main` đang đi sau 31 commit: không có `setup.ps1`, không có file
này, và con trỏ submodule còn ở bản fork cũ. Triệu chứng đúng như đã gặp ngày
04/10/2026 trên một máy khác — `.\scripts\setup.ps1` báo *"is not recognized"*,
vì file đó thật sự không tồn tại trong bản vừa clone.

Nếu đã clone rồi mới biết — quên `--recurse-submodules`, hoặc đang đứng trên
`main`:

```powershell
git checkout feat/invisible-playwright-0.25.4
git submodule update --init --recursive
```

Dòng thứ hai là bắt buộc sau khi `checkout`, không chỉ sau khi `clone`: đổi
nhánh làm đổi con trỏ submodule, và git **không** tự nạp lại nội dung submodule
cho anh — fork sẽ im lặng nằm ở bản cũ.

`setup.ps1` chạy lại bao nhiêu lần cũng được, và làm đúng bốn việc:

| Bước | Lệnh tương đương | Tải về |
|---|---|---|
| Nạp submodule | `git submodule update --init --recursive` | vài MB |
| Dựng môi trường Python | `uv sync` | ~100 MB |
| Tải engine trình duyệt | `uv run python -m invisible_playwright fetch` | **549 MB** mỗi bản engine, cộng 116 MB geoip |
| Cài phụ thuộc frontend | `npm --prefix frontend install` | ~200 MB |

Engine và geoip nằm trong cache dùng chung ở
`%LOCALAPPDATA%\invisible-playwright\`, **không nằm trong repo** — nên lần cài
đầu trên một máy mới tốn vài phút mạng, còn các lần sau thì không.

Không cần tạo `.env`: mọi thiết lập trong `backend/app/core/config.py` đều có
giá trị mặc định, và cơ sở dữ liệu SQLite tự tạo khi backend khởi động lần đầu.
`.env` chỉ cần khi muốn ghi đè thứ gì đó.

## Chạy

```powershell
uv run uvicorn --app-dir backend app.main:app --port 9000
```

```powershell
npm --prefix frontend run dev
```

Backend ở `127.0.0.1:9000`, frontend dev ở `127.0.0.1:1420`. Bản desktop thật
thì chạy `npm --prefix frontend run desktop:dev`.

## Bốn chỗ dễ sai trên máy mới

**Submodule rỗng.** `pyproject.toml` khai `invisible-playwright` là phụ thuộc
editable trỏ vào `tools/invisible_playwright`. Nếu submodule chưa nạp thì thư
mục đó trống và `uv sync` hỏng ngay ở bước dựng gói — không phải lỗi của uv.

**Bản Python.** `.python-version` ghim `3.14`. Thiếu file đó thì `uv` tự chọn
bất kỳ bản `>=3.11` nào nó tìm thấy, nên hai máy có thể dựng ra hai môi trường
khác nhau từ cùng một `uv.lock`.

**Đường dẫn clone quá sâu.** Đo ngày 04/10/2026: clone repo này vào một thư
mục có đường dẫn dài thì `git clone` báo **thành công** nhưng checkout vỡ
2506 đường dẫn — Windows giới hạn 260 ký tự và `core.longpaths` không được bật
mặc định. Clone vào đường dẫn ngắn (ví dụ `D:\tiktok_auto`) thì sạch tuyệt đối;
nếu buộc phải clone sâu thì bật `git config --global core.longpaths true` trước.

**Engine phải khớp pin của core.** `tools/invisible_playwright/pyproject.toml`
ghim `invisible_core==<x>`, và core quyết định bản engine nào được tải. Khi
submodule được cập nhật thì phải chạy lại `uv sync` rồi
`python -m invisible_playwright fetch`; nếu không, test `test_core_pin.py` sẽ
đỏ với thông báo nói rõ metadata của bản cài editable đã cũ.

## Chuyển sang một máy khác

Clone + `setup.ps1` cho anh **code chạy được nhưng rỗng**: backend lên, frontend
lên, và không có một account nào. Dữ liệu thật bị `.gitignore` nên không theo
repo đi — phải copy tay. Đo ngày 04/10/2026:

| Phải copy | Dung lượng | Thiếu thì sao |
|---|---|---|
| `backend/database.db` | 23 MB | **Thứ duy nhất thật sự bắt buộc.** Chứa account, mật khẩu, cookie, proxy, batch tag. Thiếu thì backend vẫn khởi động và tự tạo DB rỗng — không account nào. ⛔ **Đừng copy bằng `cp`/`Copy-Item`** — xem ngay dưới |
| `backend/extensions/` | 4,6 MB | Hai `.xpi`: NordVPN proxy và Omocaptcha giải captcha. Thiếu thì không giải được captcha |
| `.runtime/extension-storage/` | 1,2 MB | Phiên đã đăng nhập của extension NordVPN. Thiếu thì phải đăng nhập lại trong extension |
| `.runtime/browser-extension-settings.json` | < 1 KB | Công tắc `use_proxy` và `nordvpn_extension_enabled`. Thiếu thì về mặc định trong `config.py` |

Không cần copy `.env` — dự án này không có file đó, mọi thiết lập đang chạy bằng
giá trị mặc định, và các trường `LICENSE_*` đều để trống nên không có rào bản
quyền nào khi tự chạy.

⛔ **Copy `database.db` bằng `cp` là mất dữ liệu, mà không báo lỗi.** DB chạy ở
chế độ WAL, nên phần vừa ghi còn nằm trong `database.db-wal` cho tới lúc
checkpoint. Đo ngày 06/10/2026: file chính 23 MB, file `-wal` **4 MB**, và một
bản `cp` của riêng file chính **thiếu cả bảng `alembic_version` vừa tạo** — tức
nó cũ hơn thực tế 4 MB mà không có dấu hiệu nào. Cách đúng, chạy được cả khi app
đang mở:

```bash
python -c "import sqlite3; c=sqlite3.connect('backend/database.db'); c.execute('VACUUM INTO ?', ('database-copy.db',))"
```

`VACUUM INTO` ghi ra **một** file nhất quán và đọc cả WAL. Copy đủ ba file
`.db` + `-wal` + `-shm` cũng được, nhưng phải đồng bộ với nhau; một file thì
không có gì để lệch. `stage_friends_portable.ps1 -IncludeDatabase` đã dùng cách
này.

⛔ **Cookie đi theo IP, không đi theo máy.** Copy `database.db` sang máy khác rồi
đăng nhập từ một IP khác thì TikTok có thể từ chối chính những cookie vừa còn
tốt, hoặc đòi xác minh lại. Nếu máy mới ra Internet bằng một đường VPN **đổi IP
theo từng kết nối** thì TikTok còn giết phiên ngay giữa lúc đang chạy. Giữ đường
ra IP cố định, hoặc gán proxy cho account và bật `use_proxy`.

## Gói tự chứa, khỏi cần code

Nếu máy đích chỉ cần **chạy** chứ không cần sửa code, đóng gói hai file `.exe`
thay vì clone:

```powershell
.\scripts\build_friends_release.ps1 -Version <x.y.z>
.\scripts\stage_friends_portable.ps1 -Version <x.y.z>
```

Script thứ hai biến `release\friends\<x.y.z>\` thành thư mục **tự chứa ~0,7 GB**:
nó copy engine Firefox và geoip vào `engine\` ngay trong gói, rồi viết `CHAY.bat`
đặt `INVISIBLE_PLAYWRIGHT_CACHE_DIR` trỏ vào đó. Máy đích **không cần Internet và
không cần cài gì** — copy cả thư mục sang, bấm `CHAY.bat`.

Thêm `-IncludeDatabase` nếu muốn kèm account; mặc định **không kèm**, vì
`database.db` chứa mật khẩu và cookie của mọi nick. `CHAY.bat` chỉ đặt database
vào AppData ở lần chạy đầu và không bao giờ ghi đè bản đang có.

Hai điều gói này không lo được: khoá API OmoCaptcha vẫn phải tự nhập lần đầu, và
hai `.exe` không ký Authenticode nên SmartScreen sẽ cảnh báo.

## Test

```powershell
uv run python -m pytest backend/tests -q
```

Bộ test của fork nằm riêng trong submodule, mặc định đã loại `e2e` (cần trình
duyệt thật) và `slow` (phải dựng wheel, cần thêm gói `build`):

```powershell
cd tools\invisible_playwright
uv run python -m pytest -q
```
