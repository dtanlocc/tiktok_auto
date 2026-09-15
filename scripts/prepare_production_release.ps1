param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [ValidateSet("internal", "beta", "stable")]
    [string]$Channel,
    [Parameter(Mandatory = $true)]
    [string]$ArtifactStorageRoot,
    [string]$Python = "D:\tiktok_auto\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$artifactStorage = (Resolve-Path -LiteralPath $ArtifactStorageRoot).Path
$required = @(
    "TKAUTO_OPERATOR_CONTROL_PLANE_URL",
    "TKAUTO_DESKTOP_UPDATE_ARTIFACT_URL",
    "TKAUTO_LICENSE_PUBLIC_KEYS_JSON",
    "TKAUTO_RELEASE_PUBLIC_KEYS_JSON"
)
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Required production release variable is missing: $name"
    }
}
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("TKAUTO_CONTROL_ADMIN_TOKEN")) -and
    [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("TKAUTO_CONTROL_ADMIN_TOKEN_FILE"))) {
    throw "Set TKAUTO_CONTROL_ADMIN_TOKEN_FILE (preferred) or TKAUTO_CONTROL_ADMIN_TOKEN."
}

& (Join-Path $PSScriptRoot "build_backend_release.ps1") -Version $Version -Python $Python -RequireCommercial
$backend = Join-Path $repoRoot "release\backend\$Version\backend-$Version.exe"
& (Join-Path $PSScriptRoot "sign_windows_artifact.ps1") -Path $backend
& $Python (Join-Path $PSScriptRoot "audit_release_artifact.py") (Split-Path $backend) --component backend --version $Version
if ($LASTEXITCODE -ne 0) { throw "Signed backend release audit failed." }

& (Join-Path $PSScriptRoot "build_desktop_release.ps1") -Version $Version
$bundleRoot = Join-Path $repoRoot "frontend\src-tauri\target\release\bundle"
$installer = @(Get-ChildItem -LiteralPath (Join-Path $bundleRoot "nsis") -File -Filter "*.exe")
if ($installer.Count -ne 1) { throw "Expected exactly one NSIS installer." }
$signatureFile = "$($installer[0].FullName).sig"
if (-not (Test-Path -LiteralPath $signatureFile)) {
    throw "Updater signature does not match the NSIS artifact."
}

$storedBackend = Join-Path $artifactStorage (Split-Path $backend -Leaf)
if (Test-Path -LiteralPath $storedBackend) {
    throw "Artifact storage already contains this backend version: $storedBackend"
}
Copy-Item -LiteralPath $backend -Destination $storedBackend

$adminArgs = @(
    (Join-Path $PSScriptRoot "control_plane_admin.py"),
    "--base-url", [Environment]::GetEnvironmentVariable("TKAUTO_OPERATOR_CONTROL_PLANE_URL")
)
if (-not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("TKAUTO_CONTROL_ADMIN_TOKEN_FILE"))) {
    $adminArgs += @("--admin-token-file", [Environment]::GetEnvironmentVariable("TKAUTO_CONTROL_ADMIN_TOKEN_FILE"))
}
$adminArgs += @(
    "register-release",
    "--component", "backend",
    "--version", $Version,
    "--channel", $Channel,
    "--artifact-filename", (Split-Path $backend -Leaf),
    "--minimum-launcher-version", $Version
)
$registration = & $Python @adminArgs
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $storedBackend -Force -ErrorAction SilentlyContinue
    throw "Control-plane release registration failed; the unregistered storage copy was removed."
}

$operatorRelease = Join-Path $repoRoot "release\operator\$Version"
if (Test-Path -LiteralPath $operatorRelease) {
    throw "Operator release metadata already exists: $operatorRelease"
}
New-Item -ItemType Directory -Path $operatorRelease | Out-Null
$registration | Set-Content -LiteralPath (Join-Path $operatorRelease "backend-registration.json") -Encoding UTF8
& $Python (Join-Path $PSScriptRoot "generate_tauri_update_feed.py") `
    --version $Version `
    --artifact-url ([Environment]::GetEnvironmentVariable("TKAUTO_DESKTOP_UPDATE_ARTIFACT_URL")) `
    --signature-file $signatureFile `
    --output (Join-Path $operatorRelease "latest.json")
if ($LASTEXITCODE -ne 0) { throw "Tauri update feed generation failed." }

$customerRelease = Join-Path $repoRoot "release\customer\$Version"
if (Test-Path -LiteralPath $customerRelease) {
    throw "Customer release already exists: $customerRelease"
}
New-Item -ItemType Directory -Path $customerRelease | Out-Null
$customerInstaller = Join-Path $customerRelease "TikTokAuto-Setup-$Version.exe"
Copy-Item -LiteralPath $installer[0].FullName -Destination $customerInstaller
$customerSignature = Get-AuthenticodeSignature -LiteralPath $customerInstaller
if ($customerSignature.Status -ne "Valid") {
    throw "Final customer installer failed Authenticode verification."
}
$hash = (Get-FileHash -LiteralPath $customerInstaller -Algorithm SHA256).Hash
Set-Content -LiteralPath (Join-Path $operatorRelease "customer-installer.sha256") -Value "$hash  TikTokAuto-Setup-$Version.exe" -Encoding ASCII

Write-Host "Production release complete. Share only: $customerInstaller"
Write-Host "Publish latest.json and the signed updater artifact at the configured HTTPS URLs."
