param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$required = @(
    "TKAUTO_CONTROL_PLANE_URL",
    "TKAUTO_LICENSE_PUBLIC_KEYS_JSON",
    "TKAUTO_RELEASE_PUBLIC_KEYS_JSON",
    "TKAUTO_TAURI_UPDATER_PUBLIC_KEY",
    "TKAUTO_TAURI_UPDATER_ENDPOINT",
    "TKAUTO_WINDOWS_CERTIFICATE_THUMBPRINT",
    "TKAUTO_WINDOWS_TIMESTAMP_URL",
    "TAURI_SIGNING_PRIVATE_KEY",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"
)
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Required release environment variable is missing: $name"
    }
}

foreach ($name in @("TKAUTO_CONTROL_PLANE_URL", "TKAUTO_TAURI_UPDATER_ENDPOINT", "TKAUTO_WINDOWS_TIMESTAMP_URL")) {
    $rawUrl = [Environment]::GetEnvironmentVariable($name)
    $parsedUrl = $null
    if (-not [Uri]::TryCreate($rawUrl, [UriKind]::Absolute, [ref]$parsedUrl) -or
        $parsedUrl.Scheme -ne "https" -or
        -not [string]::IsNullOrEmpty($parsedUrl.UserInfo) -or
        -not [string]::IsNullOrEmpty($parsedUrl.Fragment)) {
        throw "$name must be a clean absolute HTTPS URL."
    }
}

foreach ($name in @("TKAUTO_LICENSE_PUBLIC_KEYS_JSON", "TKAUTO_RELEASE_PUBLIC_KEYS_JSON")) {
    try {
        $keyBundle = [Environment]::GetEnvironmentVariable($name) | ConvertFrom-Json
    }
    catch {
        throw "$name must contain valid JSON."
    }
    if ($null -eq $keyBundle -or $keyBundle.PSObject.Properties.Count -lt 1) {
        throw "$name must contain at least one public key."
    }
}

$tauriConfig = Join-Path $repoRoot "frontend\src-tauri\tauri.conf.json"
$config = Get-Content -LiteralPath $tauriConfig -Raw | ConvertFrom-Json
$config.version = $Version
$config.bundle.createUpdaterArtifacts = $true
$updater = [pscustomobject]@{
    pubkey = [Environment]::GetEnvironmentVariable("TKAUTO_TAURI_UPDATER_PUBLIC_KEY")
    endpoints = @([Environment]::GetEnvironmentVariable("TKAUTO_TAURI_UPDATER_ENDPOINT"))
}
if ($null -eq $config.plugins) {
    $config | Add-Member -NotePropertyName plugins -NotePropertyValue ([pscustomobject]@{})
}
$config.plugins | Add-Member -Force -NotePropertyName updater -NotePropertyValue $updater
$windowsSigning = [pscustomobject]@{
    certificateThumbprint = [Environment]::GetEnvironmentVariable("TKAUTO_WINDOWS_CERTIFICATE_THUMBPRINT")
    digestAlgorithm = "sha256"
    timestampUrl = [Environment]::GetEnvironmentVariable("TKAUTO_WINDOWS_TIMESTAMP_URL")
}
$config.bundle | Add-Member -Force -NotePropertyName windows -NotePropertyValue $windowsSigning
$temporaryConfig = Join-Path ([System.IO.Path]::GetTempPath()) ("tiktok-auto-tauri-" + [guid]::NewGuid().ToString("N") + ".json")
$config | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $temporaryConfig -Encoding UTF8
$previousAppVersion = [Environment]::GetEnvironmentVariable("TKAUTO_APP_VERSION", "Process")
[Environment]::SetEnvironmentVariable("TKAUTO_APP_VERSION", $Version, "Process")

Push-Location (Join-Path $repoRoot "frontend")
try {
    npm.cmd run desktop:build -- --config $temporaryConfig
    if ($LASTEXITCODE -ne 0) { throw "Tauri desktop compilation failed." }
}
finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable("TKAUTO_APP_VERSION", $previousAppVersion, "Process")
    Remove-Item -LiteralPath $temporaryConfig -Force -ErrorAction SilentlyContinue
}

$desktopExecutable = Join-Path $repoRoot "frontend\src-tauri\target\release\tiktok-auto-desktop.exe"
$bundleRoot = Join-Path $repoRoot "frontend\src-tauri\target\release\bundle"
$installers = @(Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Filter "*.exe")
if (-not (Test-Path -LiteralPath $desktopExecutable) -or $installers.Count -lt 1) {
    throw "Desktop release did not produce the executable and NSIS installer."
}
foreach ($signedFile in @($desktopExecutable) + @($installers.FullName)) {
    $signature = Get-AuthenticodeSignature -LiteralPath $signedFile
    if ($signature.Status -ne "Valid") {
        throw "Authenticode verification failed for $signedFile ($($signature.Status))."
    }
}
$updateSignatures = @(Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Filter "*.sig")
if ($updateSignatures.Count -lt 1) {
    throw "Tauri updater signature artifact was not produced."
}

Write-Host "Desktop installer, Authenticode signatures, and updater artifacts verified under $bundleRoot."
