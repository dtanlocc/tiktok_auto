param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [string]$Python = "D:\tiktok_auto\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# ⛔ CARGO IS PUT ON PATH HERE RATHER THAN ASSUMED TO BE THERE. The Tauri step
# below shells out through npm, and Tauri spawns `cargo metadata` to find the
# workspace. Measured 04/10/2026 packaging 0.1.10: that spawn answered "program
# not found" and killed the release AFTER the backend had compiled and passed
# its smoke test - twenty minutes to reach a one-line failure - while the same
# `cargo metadata` run by hand in the same shell exited 0, and both PowerShell
# and node could spawn cargo. The run that worked differed in one way only:
# .cargo\bin had been prepended to PATH.
#
# ⛔ SO THE CAUSE IS NOT PROVEN, and this is not written as if it were. What is
# certain is that the build must not depend on an ambient PATH entry it never
# checks: cargo installed outside rustup lands in .cargo\bin and nothing puts it
# on a fresh machine's PATH for a non-interactive shell. Prepending it is cheap,
# and refusing early with a sentence about rust beats failing after Nuitka.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe")) {
    $env:PATH = $cargoBin + ";" + $env:PATH
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo khong co tren PATH va khong thay trong $cargoBin. " +
          "Cai Rust (https://rustup.rs) roi chay lai; chang Tauri can no."
}
$packageRoot = Join-Path $repoRoot ("release\friends\" + $Version)
$archive = Join-Path $repoRoot ("release\friends\TikTokAuto-Friends-" + $Version + ".zip")
if ((Test-Path -LiteralPath $packageRoot) -or (Test-Path -LiteralPath $archive)) {
    throw "Friends release already exists; refusing to overwrite version $Version."
}
New-Item -ItemType Directory -Path $packageRoot | Out-Null

$extensionStaging = Join-Path ([System.IO.Path]::GetTempPath()) ("tiktok-auto-friends-extension-" + [guid]::NewGuid().ToString("N"))
$extensionSource = Join-Path $repoRoot "backend\extensions\omocaptcha_auto_solve_captcha-1.7.7.xpi"
$entrypoint = Join-Path $repoRoot "backend\friends_entrypoint.py"
$icon = Join-Path $repoRoot "frontend\src-tauri\icons\icon.ico"
if (-not (Test-Path -LiteralPath $extensionSource -PathType Leaf)) {
    throw "Signed OmoCaptcha XPI was not found: $extensionSource"
}
New-Item -ItemType Directory -Path $extensionStaging | Out-Null
$stagedExtension = Join-Path $extensionStaging (Split-Path -Leaf $extensionSource)
Copy-Item -LiteralPath $extensionSource -Destination $stagedExtension
$sourceExtensionHash = (Get-FileHash -LiteralPath $extensionSource -Algorithm SHA256).Hash
$stagedExtensionHash = (Get-FileHash -LiteralPath $stagedExtension -Algorithm SHA256).Hash
if ($sourceExtensionHash -ne $stagedExtensionHash) {
    throw "Staged OmoCaptcha XPI differs from the signed source file."
}
Write-Host "Preserved signed OmoCaptcha XPI: $sourceExtensionHash"

$arguments = @(
    "-m", "nuitka",
    "--mode=onefile",
    "--assume-yes-for-downloads",
    "--include-package=app",
    "--include-package=invisible_playwright",
    "--include-package-data=invisible_playwright",
    "--include-package-data=invisible_core",
    "--include-package=pywinauto",
    # pywinauto imports comtypes.stream/client dynamically when wrapping the
    # cross-process File name Edit. Nuitka cannot discover that import from
    # static analysis, but the packaged native chooser requires it.
    "--include-package=comtypes",
    "--enable-plugin=tk-inter",
    "--include-package=tkinter",
    "--include-module=_tkinter",
    "--nofollow-import-to=*.tests",
    "--noinclude-pytest-mode=nofollow",
    "--noinclude-unittest-mode=nofollow",
    "--noinclude-pydoc-mode=nofollow",
    "--python-flag=no_docstrings",
    "--python-flag=no_asserts",
    "--python-flag=isolated",
    "--python-flag=safe_path",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$icon",
    "--file-version=$Version",
    "--product-version=$Version",
    "--product-name=TikTok Auto Friends Backend",
    "--file-description=TikTok Auto Friends Backend",
    "--output-dir=$packageRoot",
    "--output-filename=TikTokAuto-Backend.exe",
    "--remove-output",
    "--include-data-dir=$extensionStaging=extensions",
    # Alembic doc file revision tu dia va KHONG import chung, nen Nuitka
    # khong tu thay. Thieu dong nay thi ban .exe chay den buoc dong bo
    # schema roi bao khong tim thay thu muc script.
    "--include-data-dir=$repoRoot\backend\migrations=migrations",
    $entrypoint
)

Push-Location (Join-Path $repoRoot "backend")
try {
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Friends backend compilation failed." }
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $extensionStaging) {
        Remove-Item -LiteralPath $extensionStaging -Recurse -Force
    }
}

$backend = Join-Path $packageRoot "TikTokAuto-Backend.exe"
& $Python (Join-Path $repoRoot "scripts\smoke_backend_binary.py") $backend `
    --version $Version --friends --expected-extension $extensionSource `
    --require-tk-runtime --test-native-upload
if ($LASTEXITCODE -ne 0) { throw "Friends backend smoke test failed." }
$backendHash = (Get-FileHash -LiteralPath $backend -Algorithm SHA256).Hash

$previousFriends = [Environment]::GetEnvironmentVariable("TKAUTO_FRIEND_BUILD", "Process")
$previousHash = [Environment]::GetEnvironmentVariable("TKAUTO_FRIEND_BACKEND_SHA256", "Process")
$previousVersion = [Environment]::GetEnvironmentVariable("TKAUTO_APP_VERSION", "Process")
$previousFrontendFriends = [Environment]::GetEnvironmentVariable("VITE_TKAUTO_FRIEND_BUILD", "Process")
$tauriConfigSource = Join-Path $repoRoot "frontend\src-tauri\tauri.conf.json"
$tauriConfig = Get-Content -LiteralPath $tauriConfigSource -Raw | ConvertFrom-Json
$tauriConfig.version = $Version
$tauriConfig.productName = "TikTok Auto Friends"
$temporaryTauriConfig = Join-Path ([System.IO.Path]::GetTempPath()) ("tiktok-auto-friends-tauri-" + [guid]::NewGuid().ToString("N") + ".json")
$tauriConfig | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $temporaryTauriConfig -Encoding UTF8
[Environment]::SetEnvironmentVariable("TKAUTO_FRIEND_BUILD", "1", "Process")
[Environment]::SetEnvironmentVariable("TKAUTO_FRIEND_BACKEND_SHA256", $backendHash, "Process")
[Environment]::SetEnvironmentVariable("TKAUTO_APP_VERSION", $Version, "Process")
[Environment]::SetEnvironmentVariable("VITE_TKAUTO_FRIEND_BUILD", "1", "Process")
Push-Location (Join-Path $repoRoot "frontend")
try {
    npm.cmd run desktop:build -- --no-bundle --config $temporaryTauriConfig
    if ($LASTEXITCODE -ne 0) { throw "Friends desktop compilation failed." }
}
finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable("TKAUTO_FRIEND_BUILD", $previousFriends, "Process")
    [Environment]::SetEnvironmentVariable("TKAUTO_FRIEND_BACKEND_SHA256", $previousHash, "Process")
    [Environment]::SetEnvironmentVariable("TKAUTO_APP_VERSION", $previousVersion, "Process")
    [Environment]::SetEnvironmentVariable("VITE_TKAUTO_FRIEND_BUILD", $previousFrontendFriends, "Process")
    Remove-Item -LiteralPath $temporaryTauriConfig -Force -ErrorAction SilentlyContinue
}

$desktopSource = Join-Path $repoRoot "frontend\src-tauri\target\release\tiktok-auto-desktop.exe"
$desktop = Join-Path $packageRoot "TikTokAuto-Friends.exe"
Copy-Item -LiteralPath $desktopSource -Destination $desktop
$desktopHash = (Get-FileHash -LiteralPath $desktop -Algorithm SHA256).Hash

@"
TIKTOK AUTO FRIENDS $Version

1. Giai nen TOAN BO file ZIP vao mot thu muc.
2. Giu TikTokAuto-Friends.exe va TikTokAuto-Backend.exe cung thu muc.
3. Chay TikTokAuto-Friends.exe. Ban Friends khong yeu cau license key.
4. Lan dau, nhap API key OmoCaptcha cua nguoi dung de ghi vao profile moi.
5. Neu Windows SmartScreen canh bao vi ban thu nghiem chua ky Authenticode,
   chon More info > Run anyway chi khi checksum khop SHA256SUMS.txt.

Du lieu moi may nam trong AppData rieng. Goi nay khong chua database, cookie,
source Python/TypeScript hay private key. Theo yeu cau, backend co kem NGUYEN BAN
XPI OmoCaptcha tu may dong goi, bao gom cau hinh co san ben trong extension.
Chi chia se goi nay voi nguoi ban tin cay; doi API key neu khong muon dung chung.
Day la ban chia se ban be, khong phai ban thuong mai va khong co auto-update.
"@ | Set-Content -LiteralPath (Join-Path $packageRoot "README.txt") -Encoding UTF8
@(
    "$desktopHash  TikTokAuto-Friends.exe",
    "$backendHash  TikTokAuto-Backend.exe"
) | Set-Content -LiteralPath (Join-Path $packageRoot "SHA256SUMS.txt") -Encoding ASCII

& $Python (Join-Path $repoRoot "scripts\audit_friends_package.py") $packageRoot
if ($LASTEXITCODE -ne 0) { throw "Friends package audit failed." }
& $Python (Join-Path $repoRoot "scripts\check_removed_secret.py")
if ($LASTEXITCODE -ne 0) { throw "Removed-secret audit failed." }

Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $archive -CompressionLevel Optimal
if (-not (Test-Path -LiteralPath $archive)) { throw "Friends ZIP was not created." }
Write-Host "Friends package ready: $archive"
