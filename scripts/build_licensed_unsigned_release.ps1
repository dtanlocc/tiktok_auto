# Ban CO LICENSE nhung CHUA KY: de thu tron luong key truoc khi mua chung chi.
#
# ⛔ KHAC GI SO VOI build_desktop_release.ps1. Ban release thuong mai doi 9 bien
# moi truong roi moi chay, va cuoi cung con kiem chu ky Authenticode - trong do
# co TKAUTO_WINDOWS_CERTIFICATE_THUMBPRINT, tuc mot chung chi ky code phai MUA.
# Script nay bo dung hai thu: **ky so** va **auto-update**.
#
# ⛔ VA NO KHONG LAM YEU PHAN LICENSE. Da doc lib.rs: phia Rust chi doc ba bien
# lien quan license luc bien dich - TKAUTO_CONTROL_PLANE_URL,
# TKAUTO_LICENSE_PUBLIC_KEYS_JSON, TKAUTO_RELEASE_PUBLIC_KEYS_JSON. Cac bien ky
# so va updater **khong he duoc code doc**; chung chi de script kia ghi vao
# config Tauri. Nen bo chung khong cham vao duong kiem lease.
#
# Cai mat that su: khong ky thi SmartScreen canh bao, va khong co auto-update.
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    # Thu muc do initialize_operator_environment.py tao ra. Chi doc file khoa
    # CONG KHAI tu day; khoa rieng khong bao gio vao ban build.
    [Parameter(Mandatory = $true)]
    [string]$OperatorDirectory,

    [Parameter(Mandatory = $true)]
    [string]$ControlPlaneUrl,

    [string]$Python = "D:\tiktok_auto\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$operator = (Resolve-Path -LiteralPath $OperatorDirectory).Path

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

# ⛔ URL VAN PHAI LA HTTPS SACH. Khong phai de cho giong script kia, ma vi
# ca ba tang deu tu choi thu khac: config control plane, scripts/
# control_plane_admin.py, va chinh launcher. Build mot launcher mang URL http
# thi no se tu choi chinh no luc chay - that bai muon hon va kho hieu hon.
$parsed = [Uri]$null
if (-not [Uri]::TryCreate($ControlPlaneUrl, [UriKind]::Absolute, [ref]$parsed) -or
    $parsed.Scheme -ne "https" -or
    -not [string]::IsNullOrEmpty($parsed.UserInfo) -or
    -not [string]::IsNullOrEmpty($parsed.Fragment) -or
    -not [string]::IsNullOrEmpty($parsed.Query)) {
    throw "ControlPlaneUrl phai la HTTPS sach (khong user/pass, khong query, khong fragment)."
}

Step "Doc khoa cong khai tu thu muc operator"
$bundlePath = Join-Path $operator "desktop-public-keys.json"
if (-not (Test-Path -LiteralPath $bundlePath)) {
    throw "Thieu desktop-public-keys.json trong $operator."
}
$bundle = Get-Content -LiteralPath $bundlePath -Raw | ConvertFrom-Json
$licenseKeys = $bundle.license_public_keys | ConvertTo-Json -Compress -Depth 10
$releaseKeys = $bundle.release_public_keys | ConvertTo-Json -Compress -Depth 10
foreach ($pair in @(@("license", $licenseKeys), @("release", $releaseKeys))) {
    if ([string]::IsNullOrWhiteSpace($pair[1]) -or $pair[1] -eq "{}" -or $pair[1] -eq "null") {
        throw "Bundle $($pair[0])_public_keys trong desktop-public-keys.json dang rong."
    }
}
Write-Host "  license keys: $(($bundle.license_public_keys.PSObject.Properties | ForEach-Object Name) -join ', ')"
Write-Host "  release keys: $(($bundle.release_public_keys.PSObject.Properties | ForEach-Object Name) -join ', ')"

# Cargo phai co tren PATH truoc khi toi chang Tauri; xem comment trong
# build_friends_release.ps1 ve lan that bai sau 20 phut.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe")) {
    $env:PATH = $cargoBin + ";" + $env:PATH
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo khong co tren PATH va khong thay trong $cargoBin."
}

$packageRoot = Join-Path $repoRoot ("release\licensed-unsigned\" + $Version)
if (Test-Path -LiteralPath $packageRoot) {
    throw "Da ton tai: $packageRoot"
}

Step "Build backend co license (secure_entrypoint)"
# Dung lai nguyen script that, khong viet lai: no da co buoc lam sach
# credential trong extension va buoc audit artifact.
& (Join-Path $repoRoot "scripts\build_backend_release.ps1") -Version $Version -Python $Python
$backendExe = Join-Path $repoRoot ("release\backend\" + $Version + "\backend-" + $Version + ".exe")
if (-not (Test-Path -LiteralPath $backendExe)) {
    throw "Khong thay backend vua build: $backendExe"
}

Step "Build launcher desktop (co license, KHONG ky, KHONG updater)"
$tauriConfigSource = Join-Path $repoRoot "frontend\src-tauri\tauri.conf.json"
$tauriConfig = Get-Content -LiteralPath $tauriConfigSource -Raw | ConvertFrom-Json
$tauriConfig.version = $Version
# ⛔ KHONG dat plugins.updater va KHONG dat bundle.windows: do chinh la hai thu
# bi bo. Them vao se doi hoi khoa updater va chung chi ky.
$temporaryConfig = Join-Path ([System.IO.Path]::GetTempPath()) (
    "tkauto-licensed-unsigned-" + [guid]::NewGuid().ToString("N") + ".json")
$tauriConfig | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $temporaryConfig -Encoding UTF8

$previous = @{
    app = [Environment]::GetEnvironmentVariable("TKAUTO_APP_VERSION", "Process")
    url = [Environment]::GetEnvironmentVariable("TKAUTO_CONTROL_PLANE_URL", "Process")
    lic = [Environment]::GetEnvironmentVariable("TKAUTO_LICENSE_PUBLIC_KEYS_JSON", "Process")
    rel = [Environment]::GetEnvironmentVariable("TKAUTO_RELEASE_PUBLIC_KEYS_JSON", "Process")
    frd = [Environment]::GetEnvironmentVariable("TKAUTO_FRIEND_BUILD", "Process")
}
[Environment]::SetEnvironmentVariable("TKAUTO_APP_VERSION", $Version, "Process")
[Environment]::SetEnvironmentVariable("TKAUTO_CONTROL_PLANE_URL", $ControlPlaneUrl, "Process")
[Environment]::SetEnvironmentVariable("TKAUTO_LICENSE_PUBLIC_KEYS_JSON", $licenseKeys, "Process")
[Environment]::SetEnvironmentVariable("TKAUTO_RELEASE_PUBLIC_KEYS_JSON", $releaseKeys, "Process")
# ⛔ PHAI XOA, khong phai de nguyen: neu bien nay con sot lai tu mot lan build
# friends truoc do trong cung shell, launcher se di duong friends - tim backend
# canh no theo SHA-256 - va bo qua toan bo phan license ma khong bao gi.
[Environment]::SetEnvironmentVariable("TKAUTO_FRIEND_BUILD", $null, "Process")

Push-Location (Join-Path $repoRoot "frontend")
try {
    Run npm.cmd @("run", "desktop:build", "--", "--no-bundle", "--config", $temporaryConfig)
    if ($LASTEXITCODE -ne 0) { throw "Build launcher that bai." }
}
finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable("TKAUTO_APP_VERSION", $previous.app, "Process")
    [Environment]::SetEnvironmentVariable("TKAUTO_CONTROL_PLANE_URL", $previous.url, "Process")
    [Environment]::SetEnvironmentVariable("TKAUTO_LICENSE_PUBLIC_KEYS_JSON", $previous.lic, "Process")
    [Environment]::SetEnvironmentVariable("TKAUTO_RELEASE_PUBLIC_KEYS_JSON", $previous.rel, "Process")
    [Environment]::SetEnvironmentVariable("TKAUTO_FRIEND_BUILD", $previous.frd, "Process")
    Remove-Item -LiteralPath $temporaryConfig -Force -ErrorAction SilentlyContinue
}

$desktopSource = Join-Path $repoRoot "frontend\src-tauri\target\release\tiktok-auto-desktop.exe"
if (-not (Test-Path -LiteralPath $desktopSource)) {
    throw "Khong thay launcher vua build: $desktopSource"
}

Step "Gom goi"
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
$launcher = Join-Path $packageRoot "TikTokAuto.exe"
Copy-Item -LiteralPath $desktopSource -Destination $launcher
# ⛔ BACKEND O DAY LA DE OPERATOR DANG KY, KHONG PHAI DE KHACH CHAY. Ban co
# license khong mang backend theo: launcher doc lease roi nap backend tu
# %LOCALAPPDATA%\...\artifacts va doi chieu sha256 + size voi manifest da ky
# (lib.rs:trusted_installed_backend). Nen file nay phai di qua
# `control_plane_admin.py register-release`, roi client tu tai ve.
$backendCopy = Join-Path $packageRoot ("backend-" + $Version + ".exe")
Copy-Item -LiteralPath $backendExe -Destination $backendCopy

$launcherHash = (Get-FileHash -LiteralPath $launcher -Algorithm SHA256).Hash
$backendHash = (Get-FileHash -LiteralPath $backendCopy -Algorithm SHA256).Hash
@(
    "$launcherHash  TikTokAuto.exe",
    "$backendHash  backend-$Version.exe"
) | Set-Content -LiteralPath (Join-Path $packageRoot "SHA256SUMS.txt") -Encoding ASCII

@"
TIKTOK AUTO $Version - ban CO LICENSE, CHUA KY

Control plane: $ControlPlaneUrl

TikTokAuto.exe       -> gui cho khach. Can key de kich hoat.
backend-$Version.exe -> KHONG gui cho khach. Dung de operator dang ky:
                        control_plane_admin.py register-release ...
                        Launcher se tu tai ban da dang ky ve AppData va doi
                        chieu sha256 voi manifest da ky truoc khi chay.

Chua ky Authenticode nen Windows SmartScreen se canh bao: More info >
Run anyway, va chi khi checksum khop SHA256SUMS.txt.
Ban nay KHONG co auto-update.
"@ | Set-Content -LiteralPath (Join-Path $packageRoot "README.txt") -Encoding UTF8

Write-Host ""
Write-Host "Xong: $packageRoot" -ForegroundColor Green
Write-Host "  TikTokAuto.exe        $([math]::Round((Get-Item $launcher).Length/1MB,0)) MB"
Write-Host "  backend-$Version.exe  $([math]::Round((Get-Item $backendCopy).Length/1MB,0)) MB (de dang ky)"

# exit 0 tuong minh: xem comment trong stage_friends_portable.ps1 ve robocopy.
exit 0
