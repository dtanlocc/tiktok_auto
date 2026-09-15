param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,
    [string]$Python = "D:\tiktok_auto\.venv\Scripts\python.exe",
    [switch]$RequireCommercial
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$commercialVersion = (& $Python -c "from nuitka.Version import getCommercialVersion; print(getCommercialVersion() or '')" 2>&1) -join ""
if ($LASTEXITCODE -ne 0) { throw "Nuitka is not available in the selected Python environment." }
if ($RequireCommercial -and [string]::IsNullOrWhiteSpace($commercialVersion)) {
    throw "Production release requires Nuitka Commercial; the selected environment reports Commercial: None."
}
$outputRoot = Join-Path $repoRoot ("release\backend\" + $Version)
if (Test-Path -LiteralPath $outputRoot) {
    throw "Release output already exists; refusing to overwrite: $outputRoot"
}
New-Item -ItemType Directory -Path $outputRoot | Out-Null

$entrypoint = Join-Path $repoRoot "backend\secure_entrypoint.py"
$icon = Join-Path $repoRoot "frontend\src-tauri\icons\icon.ico"
$extensionStaging = Join-Path ([System.IO.Path]::GetTempPath()) ("tiktok-auto-extension-release-" + [guid]::NewGuid().ToString("N"))
$extensionSource = Join-Path $repoRoot "backend\extensions"
& $Python (Join-Path $repoRoot "scripts\sanitize_extensions_for_release.py") $extensionSource $extensionStaging
if ($LASTEXITCODE -ne 0) { throw "Extension credential sanitization failed." }
$arguments = @(
    "-m", "nuitka",
    "--mode=onefile",
    "--assume-yes-for-downloads",
    "--include-package=app",
    "--include-package=invisible_playwright",
    "--include-package-data=invisible_playwright",
    "--include-package-data=invisible_core",
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
    "--product-name=TikTok Auto Backend",
    "--file-description=Licensed TikTok Auto Backend",
    "--output-dir=$outputRoot",
    "--output-filename=backend-$Version.exe",
    "--remove-output"
    "--include-data-dir=$extensionStaging=extensions"
)
if ($RequireCommercial) {
    # Commercial presence alone is not enough: this plugin applies white-box
    # protection to program constants and identifiers in the customer binary.
    $arguments += "--enable-plugin=data-hiding"
}
$arguments += $entrypoint

Push-Location (Join-Path $repoRoot "backend")
try {
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Nuitka backend compilation failed." }
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $extensionStaging) {
        Remove-Item -LiteralPath $extensionStaging -Recurse -Force
    }
}

& $Python (Join-Path $repoRoot "scripts\audit_release_artifact.py") `
    $outputRoot --component backend --version $Version `
    --secret-env OMOCAPTCHA_KEY `
    --secret-env TKAUTO_CONTROL_ADMIN_TOKEN `
    --secret-env TKAUTO_CONTROL_LICENSE_KEY_PEPPER `
    --secret-env TKAUTO_CONTROL_DOWNLOAD_GRANT_SECRET
if ($LASTEXITCODE -ne 0) { throw "Backend release audit failed." }

Write-Host "Backend release ready: $outputRoot"
