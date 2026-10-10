# Dung moi truong cho mot ban clone moi. Chay lai bao nhieu lan cung duoc:
# moi buoc tu bo qua phan da co.
#
# ⛔ THU TU KHONG DOI CHO DUOC, nhung khong con vi submodule nao. Engine chi
# tai duoc SAU `uv sync`, vi ban engine nao duoc tai do `invisible_core` trong
# moi truong vua dung quyet dinh, chu khong phai do script nay. Truoc
# 10/10/2026 o day con mot buoc `git submodule update` nap fork
# invisible_playwright; du an da chuyen sang ban chinh chu tren PyPI nen buoc
# do bien mat, va `git` khong con la dieu kien de chay setup.
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

# ⛔ A NATIVE COMMAND'S STDERR IS NOT AN ERROR, and Windows PowerShell 5.1
# disagrees. The moment the caller captures or redirects output, every stderr
# line from an exe becomes an ErrorRecord, and with the "Stop" preference above
# that aborts the script although the exe returned 0. Measured 04/10/2026 in a
# fresh clone: `uv sync` writes "Resolved 59 packages" to stderr and exits 0, a
# plain run of this script finished fine, and `setup.ps1 2>&1 | ...` died right
# there - so it worked for everyone except whoever kept a log of the setup,
# which is exactly what a first run on a new machine deserves.
#
# So the preference is lifted around the call and the EXIT CODE is what decides.
#
# ⛔ AND IT RETURNS NOTHING. Returning the exit code looks tidier and is wrong:
# the command's own stdout is already on this function's pipeline, so the caller
# would receive the output lines AND the code, and `(Run ...) -ne 0` compares an
# ARRAY to zero - which in PowerShell filters rather than tests, and is truthy
# whenever the command printed anything at all. Measured in the same run: `uv
# sync` prints to stderr, so that call passed, and `fetch` prints a path to
# stdout, so a successful fetch was reported as a failure. The caller reads
# $LASTEXITCODE, which the native call sets regardless.
function Run {
    param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $previous }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "Khong tim thay 'uv'. Cai o https://docs.astral.sh/uv/ roi chay lai."
}

Step "Dung moi truong Python theo uv.lock"
Run uv @("sync")
if ($LASTEXITCODE -ne 0) {
    throw "uv sync that bai. Neu loi la 'Access is denied' tren mot file .pyd " +
          "thi co tien trinh dang dung .venv (thuong la backend) - tat no roi chay lai."
}

if (-not $SkipEngine) {
    Step "Tai engine trinh duyet (549 MB moi ban, cache o %LOCALAPPDATA%)"
    Run uv @("run", "python", "-m", "invisible_playwright", "fetch")
    if ($LASTEXITCODE -ne 0) { throw "Tai engine that bai." }
} else {
    Step "Bo qua engine (-SkipEngine)"
}

if (-not $SkipFrontend) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Step "Cai phu thuoc frontend"
        Run npm @("--prefix", "frontend", "install")
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
