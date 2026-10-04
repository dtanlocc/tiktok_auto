# tiktok_auto

Backend FastAPI + frontend React/Tauri, điều khiển trình duyệt qua fork
[`invisible_playwright`](https://github.com/dtanlocc/invisible_playwright-custom)
được gắn vào repo này dưới dạng **git submodule**.

## Cài trên máy mới

Cần có sẵn: [uv](https://docs.astral.sh/uv/), Git, và Node 20+ (chỉ để chạy
frontend). Không cần tự cài Python — `uv` tải đúng bản ghi trong
`.python-version`.

```powershell
git clone --recurse-submodules https://github.com/dtanlocc/tiktok_auto.git
cd tiktok_auto
.\scripts\setup.ps1
```

Nếu đã clone mà quên `--recurse-submodules`, chạy thêm:

```powershell
git submodule update --init --recursive
```

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

## Ba chỗ dễ sai trên máy mới

**Submodule rỗng.** `pyproject.toml` khai `invisible-playwright` là phụ thuộc
editable trỏ vào `tools/invisible_playwright`. Nếu submodule chưa nạp thì thư
mục đó trống và `uv sync` hỏng ngay ở bước dựng gói — không phải lỗi của uv.

**Bản Python.** `.python-version` ghim `3.14`. Thiếu file đó thì `uv` tự chọn
bất kỳ bản `>=3.11` nào nó tìm thấy, nên hai máy có thể dựng ra hai môi trường
khác nhau từ cùng một `uv.lock`.

**Engine phải khớp pin của core.** `tools/invisible_playwright/pyproject.toml`
ghim `invisible_core==<x>`, và core quyết định bản engine nào được tải. Khi
submodule được cập nhật thì phải chạy lại `uv sync` rồi
`python -m invisible_playwright fetch`; nếu không, test `test_core_pin.py` sẽ
đỏ với thông báo nói rõ metadata của bản cài editable đã cũ.

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
