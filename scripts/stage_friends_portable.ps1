# Bien mot goi friends da build thanh thu muc TU CHUA: copy sang may khac la
# chay duoc, khong can Internet, khong can cai gi.
#
# ⛔ ENGINE KHONG NAM TRONG EXE. TikTokAuto-Backend.exe chi 45 MB; mot ban engine
# Firefox la 549 MB cong 116 MB geoip, va binh thuong no duoc TAI VE lan dau
# chay, vao %LOCALAPPDATA%. Nen mot goi vua build xong van can mang.
#
# ⛔ KHONG COPY VAO AppData LUC CHAY. invisible_core doc bien
# INVISIBLE_PLAYWRIGHT_CACHE_DIR va coi do la cache_root (download.py), nen
# engine nam NGAY TRONG thu muc goi va duoc dung truc tiep - khong nhan doi
# 665 MB tren may dich, va thu muc chay duoc tu ca USB. Da do 04/10/2026: dat
# bien do roi `invisible_playwright fetch` tra ve dung engine trong thu muc
# duoc chi, khong tai gi.
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [string]$Python = "D:\tiktok_auto\.venv\Scripts\python.exe",
    # ⛔ MAC DINH KHONG KEM DATABASE. No chua mat khau va cookie cua moi
    # account; README cua goi noi ro goi khong chua database. Chi bat khi anh
    # dem goi sang MAY CUA CHINH MINH.
    [switch]$IncludeDatabase
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Step([string]$text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

# Stderr cua lenh native khong phai loi; xem comment trong setup.ps1.
function Run {
    param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments = @())
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @Arguments } finally { $ErrorActionPreference = $previous }
}

$packageRoot = Join-Path $repoRoot ("release\friends\" + $Version)
$backendExe = Join-Path $packageRoot "TikTokAuto-Backend.exe"
$desktopExe = Join-Path $packageRoot "TikTokAuto-Friends.exe"
foreach ($needed in @($packageRoot, $backendExe, $desktopExe)) {
    if (-not (Test-Path -LiteralPath $needed)) {
        throw "Khong thay '$needed'. Chay build_friends_release.ps1 -Version $Version truoc."
    }
}

Step "Tim cache engine tren may nay"
$cacheRoot = (& $Python -c "from invisible_core.download import cache_root; print(cache_root())").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($cacheRoot)) {
    throw "Khong hoi duoc cache_root tu invisible_core."
}
$sealedEngine = (& $Python -c "from invisible_core.download import cache_dir_for_seal; print(cache_dir_for_seal())").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sealedEngine)) {
    throw "Engine chua co trong cache: $sealedEngine`n" +
          "Chay truoc: uv run python -m invisible_playwright fetch"
}
Write-Host "  cache  : $cacheRoot"
Write-Host "  engine : $(Split-Path -Leaf $sealedEngine)"

Step "Copy engine + geoip + fonts vao goi"
$engineTarget = Join-Path $packageRoot "engine"
New-Item -ItemType Directory -Path $engineTarget -Force | Out-Null
$engineName = Split-Path -Leaf $sealedEngine
# robocopy: /MIR de chay lai khong ton thoi gian, /NFL /NDL /NJH /NJS cho log gon.
# Ma thoat 0-7 la thanh cong, >=8 moi la loi.
Run robocopy @($sealedEngine, (Join-Path $engineTarget $engineName), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copy engine that bai (robocopy $LASTEXITCODE)." }
foreach ($extra in @("geoip", "fonts")) {
    $source = Join-Path $cacheRoot $extra
    if (Test-Path -LiteralPath $source) {
        Run robocopy @($source, (Join-Path $engineTarget $extra), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copy $extra that bai (robocopy $LASTEXITCODE)." }
        Write-Host "  da kem: $extra"
    }
}

Step "Dat san bios.txt canh exe"
# ⛔ CANH EXE, VI DO LA CHO BACKEND DOC. Khi bien dich, _bios_file_path() lay
# thu muc cua executable - khong phai thu muc nguon, va khong phai thu muc giai
# nen tam cua onefile (no bi xoa moi lan thoat). Dat san mot ban o day de nguoi
# dung co file de sua ngay, thay vi doi backend tu sinh ban mac dinh.
$biosTarget = Join-Path $packageRoot "bios.txt"
if (Test-Path -LiteralPath $biosTarget) {
    Write-Host "  da co bios.txt trong goi - giu nguyen, khong ghi de."
} else {
    $biosSource = Join-Path $repoRoot "backend\bios.txt"
    if (Test-Path -LiteralPath $biosSource) {
        Copy-Item -LiteralPath $biosSource -Destination $biosTarget
        $lines = @(Get-Content -LiteralPath $biosTarget | Where-Object { $_.Trim() })
        Write-Host "  da copy tu backend\bios.txt ($($lines.Count) dong)"
    } else {
        Set-Content -LiteralPath $biosTarget -Encoding UTF8 -Value @(
            "Keep moving forward",
            "Living life one day at a time"
        )
        Write-Host "  khong thay backend\bios.txt -> da viet 2 dong mau"
    }
}

if ($IncludeDatabase) {
    Step "Kem database (mat khau + cookie!)"
    $sourceDb = Join-Path $repoRoot "backend\database.db"
    if (-not (Test-Path -LiteralPath $sourceDb)) { throw "Khong thay $sourceDb." }
    $dataDir = Join-Path $packageRoot "data"
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $targetDb = Join-Path $dataDir "database.db"
    Remove-Item -LiteralPath $targetDb -Force -ErrorAction SilentlyContinue

    # ⛔ COPY-ITEM LAM MAT DU LIEU, KHONG BAO LOI. DB chay o che do WAL
    # (PRAGMA journal_mode=WAL trong connection.py), nen nhung gi vua ghi con
    # nam trong database.db-wal cho den luc checkpoint. Do ngay 06/10/2026:
    # database.db 23 MB va database.db-wal 4 MB, va mot ban Copy-Item cua rieng
    # file chinh KHONG co bang alembic_version vua tao - tuc no la mot ban chup
    # cu hon thuc te 4 MB, ma khong co dau hieu gi.
    #
    # VACUUM INTO ghi ra MOT file nhat quan, doc ca WAL, va an toan ngay khi DB
    # dang duoc dung. Copy ca ba file .db/-wal/-shm cung duoc nhung phai dong
    # bo nhau; mot file thi khong co gi de lech.
    Run $Python @("-c",
        "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('VACUUM INTO ?',(sys.argv[2],)); c.close()",
        $sourceDb, $targetDb)
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $targetDb)) {
        throw "Khong tao duoc ban sao database nhat quan (VACUUM INTO that bai)."
    }
    $sizeMb = [math]::Round((Get-Item -LiteralPath $targetDb).Length / 1MB, 0)
    Write-Host "  da kem database.db ($sizeMb MB, ban sao nhat quan ke ca WAL)"
    Write-Host "  CHI dem goi nay sang may cua chinh minh." -ForegroundColor Yellow
}

Step "Viet CHAY.bat"
# ⛔ .BAT CHU KHONG PHAI .PS1. Windows mac dinh khong cho chay file .ps1
# (ExecutionPolicy Restricted), nen mot launcher .ps1 se khong bam duoc tren
# may chua ai cau hinh - da gap dung loi do ngay 04/10/2026. File .bat thi
# khong chiu chinh sach nay.
$launcher = @'
@echo off
setlocal
cd /d "%~dp0"

rem Engine nam trong chinh thu muc nay, khong tai ve va khong copy vao AppData.
set "INVISIBLE_PLAYWRIGHT_CACHE_DIR=%~dp0engine"

if not exist "%~dp0engine" (
  echo [!] Thieu thu muc engine. Goi nay chua duoc chuan bi day du.
  echo     Chay stage_friends_portable.ps1 tren may dong goi.
  pause
  exit /b 1
)

rem Database chi duoc dat vao lan dau, va KHONG BAO GIO ghi de ban dang co.
set "APPDIR=%LOCALAPPDATA%\com.tiktokauto.desktop"
if exist "%~dp0data\database.db" (
  if not exist "%APPDIR%\database.db" (
    if not exist "%APPDIR%" mkdir "%APPDIR%"
    copy /y "%~dp0data\database.db" "%APPDIR%\database.db" >nul
    echo [+] Da dat database vao "%APPDIR%".
  ) else (
    echo [*] Da co database san tai "%APPDIR%" - giu nguyen, khong ghi de.
  )
)

echo [*] Dang mo TikTok Auto Friends...
start "" "%~dp0TikTokAuto-Friends.exe"
'@
Set-Content -LiteralPath (Join-Path $packageRoot "CHAY.bat") -Value $launcher -Encoding ASCII

$note = @"
CACH DUNG - ban tu chua

1. Copy TOAN BO thu muc nay sang may dich (giu nguyen cau truc ben trong).
2. Bam dup CHAY.bat. Khong chay truc tiep TikTokAuto-Friends.exe:
   CHAY.bat la cho tro engine vao thu muc engine\ ben canh.
3. Windows SmartScreen se canh bao vi hai exe khong ky Authenticode:
   More info > Run anyway, va chi khi checksum khop SHA256SUMS.txt.

Thu muc nay KHONG can Internet va KHONG can cai gi: engine Firefox va geoip
da nam trong engine\. Lan chay dau khong phai tai 665 MB nua.

Van con phai tu nhap: khoa API OmoCaptcha, o lan chay dau.

BIO: sua file bios.txt NGAY TRONG THU MUC NAY, moi dong la mot bio, dong trong
bi bo qua. Chuc nang doi ho so chon ngau nhien mot dong. Emoji dung duoc.
Dung dat bios.txt o cho khac - backend chi doc file canh TikTokAuto-Backend.exe.

Database: $(if ($IncludeDatabase) { "CO kem trong data\database.db, se duoc dat vao AppData lan dau chay." } else { "KHONG kem. May dich se bat dau voi 0 account." })
Cookie trong database thuoc ve IP cua may da tao ra no. Mo tu mot IP khac thi
TikTok co the tu choi cookie hoac doi xac minh lai.
"@
Set-Content -LiteralPath (Join-Path $packageRoot "CACH-DUNG.txt") -Value $note -Encoding UTF8

$total = [math]::Round(((Get-ChildItem -LiteralPath $packageRoot -Recurse -File |
    Measure-Object Length -Sum).Sum / 1GB), 2)
Write-Host ""
Write-Host "Xong. Thu muc tu chua: $packageRoot ($total GB)" -ForegroundColor Green
Write-Host "Copy ca thu muc sang may khac roi bam CHAY.bat."

# ⛔ EXIT 0 TUONG MINH. PowerShell lay $LASTEXITCODE cua lenh native cuoi cung
# lam ma thoat cua ca script, va robocopy tra ve 1 khi NO DA COPY XONG - 0 moi
# la "khong co gi de copy". Nen mot lan chay thanh cong tron ven tu bao minh la
# that bai, va bat ky ai goi script nay roi kiem $LASTEXITCODE deu bi lua. Da
# gap dung vay ngay 04/10/2026, o chinh lan chay dau tien. Cac duong loi phia
# tren dung `throw`, nen den duoc day nghia la xong.
exit 0
