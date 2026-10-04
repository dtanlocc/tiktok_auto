# Dung moi truong cho mot ban clone moi. Chay lai bao nhieu lan cung duoc:
# moi buoc tu bo qua phan da co.
#
# ⛔ THU TU KHONG DOI CHO DUOC. `uv sync` dung `tools/invisible_playwright` lam
# phu thuoc editable, nen submodule phai duoc nap TRUOC - neu khong, uv hong o
# buoc dung goi va thong bao khong he noi gi ve submodule. Va engine chi tai
# duoc SAU `uv sync`, vi ban engine nao duoc tai do `invisible_core` trong
# moi truong vua dung quyet dinh, chu khong phai do script nay.
param(
    [switch]$SkipFrontend,
    [switch]$SkipEngine
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

function Step([string]$text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "Khong tim thay 'uv'. Cai o https://docs.astral.sh/uv/ roi chay lai."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Khong tim thay 'git'."
}

Step "Nap submodule (fork invisible_playwright)"
& git submodule update --init --recursive
if ($LASTEXITCODE -ne 0) { throw "git submodule update that bai." }

$forkPyproject = Join-Path $repoRoot "tools\invisible_playwright\pyproject.toml"
if (-not (Test-Path -LiteralPath $forkPyproject)) {
    throw "Submodule tools/invisible_playwright van trong sau khi nap. " +
          "Kiem tra quyen truy cap repo dtanlocc/invisible_playwright-custom."
}

Step "Dung moi truong Python theo uv.lock"
& uv sync
if ($LASTEXITCODE -ne 0) {
    throw "uv sync that bai. Neu loi la 'Access is denied' tren mot file .pyd " +
          "thi co tien trinh dang dung .venv (thuong la backend) - tat no roi chay lai."
}

if (-not $SkipEngine) {
    Step "Tai engine trinh duyet (549 MB moi ban, cache o %LOCALAPPDATA%)"
    & uv run python -m invisible_playwright fetch
    if ($LASTEXITCODE -ne 0) { throw "Tai engine that bai." }
} else {
    Step "Bo qua engine (-SkipEngine)"
}

if (-not $SkipFrontend) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Step "Cai phu thuoc frontend"
        & npm --prefix frontend install
        if ($LASTEXITCODE -ne 0) { throw "npm install that bai." }
    } else {
        Write-Host ""
        Write-Host "Khong co 'npm' -> bo qua frontend. Backend van chay duoc." -ForegroundColor Yellow
    }
} else {
    Step "Bo qua frontend (-SkipFrontend)"
}

Write-Host ""
Write-Host "Xong. Chay bang:" -ForegroundColor Green
Write-Host "  uv run uvicorn --app-dir backend app.main:app --port 9000"
Write-Host "  npm --prefix frontend run dev"
