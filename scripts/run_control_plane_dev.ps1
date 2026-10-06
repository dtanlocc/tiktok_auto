# Chay control plane license o che do DEVELOPMENT, tren may minh.
#
# ⛔ KHONG PHAI BAN TRIEN KHAI THAT. Ban that nam o deploy/control-plane/:
# Postgres rieng tu, Caddy lam TLS, chay read-only, drop capabilities. Script
# nay chi de THU va PHAT KEY TEST tren may phat trien, va `environment` phai la
# `development` vi config tu choi SQLite khi `production`.
#
# ⛔ KHONG CO BI MAT NAO TRONG FILE NAY. Moi khoa va token duoc doc tu thu muc
# operator do `initialize_operator_environment.py` tao ra - thu muc do KHONG
# duoc nam trong repo va khong duoc commit. Script chi nhan duong dan toi no.
param(
    [Parameter(Mandatory = $true)]
    [string]$OperatorDirectory,
    [int]$Port = 9100,
    # ⛔ ĐÂY LÀ ĐỊA CHỈ NÓI CHO CLIENT, KHÔNG PHẢI THỨ UVICORN PHỤC VỤ. Config
    # đòi HTTPS **không điều kiện** (`config.py:validate_runtime`), trong khi chỉ
    # cấm `localhost` khi `production` - tức nó cố ý cho chạy local mà vẫn buộc
    # TLS. Server dev ở đây chạy HTTP thuần, nên hai thứ lệch nhau một cách có
    # chủ ý và CÓ HỆ QUẢ: thao tác admin (tạo/xem/thu hồi key) hoạt động bình
    # thường, nhưng URL tải artifact mà server trả về sẽ là https và không ai
    # phục vụ nó. Muốn chạy trọn cả bước launcher tải backend thì cần một
    # endpoint HTTPS mà client TIN - xem docs/HUONG-DAN-SU-DUNG.md.
    [string]$PublicBaseUrl = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$operator = (Resolve-Path -LiteralPath $OperatorDirectory).Path

# Kiem truoc, bao ten file thieu - thay vi de uvicorn chet voi mot loi config
# khong noi ro thieu cai gi.
$needed = @(
    "keys\lease-private.pem",
    "keys\release-private.pem",
    "secrets\admin-token",
    "secrets\license-key-pepper",
    "secrets\download-grant-secret"
)
foreach ($relative in $needed) {
    $path = Join-Path $operator $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Thieu '$relative' trong $operator. Tao thu muc operator bang:`n" +
              "  uv run python scripts\initialize_operator_environment.py <dich> " +
              "--database-host localhost --database-user u --database-name d " +
              "--lease-key-id lease-dev-2026-01 --release-key-id release-dev-2026-01"
    }
}

# Doc key id tu ten file khoa cong khai thi khong duoc - chung khong mang id.
# Lay tu desktop-public-keys.json, vi do la noi id da duoc ghi ra.
$bundlePath = Join-Path $operator "desktop-public-keys.json"
if (-not (Test-Path -LiteralPath $bundlePath)) {
    throw "Thieu desktop-public-keys.json trong $operator."
}
$bundle = Get-Content -LiteralPath $bundlePath -Raw | ConvertFrom-Json
$leaseKeyId = ($bundle.license_public_keys.PSObject.Properties | Select-Object -First 1).Name
$releaseKeyId = ($bundle.release_public_keys.PSObject.Properties | Select-Object -First 1).Name
if (-not $leaseKeyId -or -not $releaseKeyId) {
    throw "desktop-public-keys.json khong co key id nao."
}

$artifacts = Join-Path $operator "artifacts"
New-Item -ItemType Directory -Path $artifacts -Force | Out-Null

$env:TKAUTO_CONTROL_ENVIRONMENT = "development"
$env:TKAUTO_CONTROL_DATABASE_URL = "sqlite:///" + (Join-Path $operator "control_plane_dev.db").Replace('\', '/')
$env:TKAUTO_CONTROL_ADMIN_TOKEN_FILE = Join-Path $operator "secrets\admin-token"
$env:TKAUTO_CONTROL_LICENSE_KEY_PEPPER_FILE = Join-Path $operator "secrets\license-key-pepper"
$env:TKAUTO_CONTROL_DOWNLOAD_GRANT_SECRET_FILE = Join-Path $operator "secrets\download-grant-secret"
$env:TKAUTO_CONTROL_LEASE_SIGNING_KEY_ID = $leaseKeyId
$env:TKAUTO_CONTROL_LEASE_SIGNING_PRIVATE_KEY_PATH = Join-Path $operator "keys\lease-private.pem"
$env:TKAUTO_CONTROL_RELEASE_SIGNING_KEY_ID = $releaseKeyId
$env:TKAUTO_CONTROL_RELEASE_SIGNING_PRIVATE_KEY_PATH = Join-Path $operator "keys\release-private.pem"
$env:TKAUTO_CONTROL_ARTIFACT_STORAGE_ROOT = $artifacts
if ([string]::IsNullOrWhiteSpace($PublicBaseUrl)) {
    $PublicBaseUrl = "https://127.0.0.1:$Port"
}
$env:TKAUTO_CONTROL_PUBLIC_BASE_URL = $PublicBaseUrl
$env:TKAUTO_CONTROL_TRUSTED_HOSTS = "127.0.0.1,localhost"

Write-Host ""
Write-Host "Control plane (development) tren http://127.0.0.1:$Port$([char]32)" -ForegroundColor Cyan
Write-Host "  operator : $operator"
Write-Host "  lease key: $leaseKeyId"
Write-Host "  release  : $releaseKeyId"
Write-Host ""

Set-Location -LiteralPath $repoRoot
& (Join-Path $repoRoot ".venv\Scripts\python.exe") -m uvicorn `
    "control_plane.app.main:load_default_app" --factory --port $Port
