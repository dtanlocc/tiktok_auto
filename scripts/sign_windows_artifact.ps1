param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
$resolvedPath = (Resolve-Path -LiteralPath $Path).Path
$item = Get-Item -LiteralPath $resolvedPath
if ($item.PSIsContainer -or $item.Extension -ne ".exe") {
    throw "Only an existing Windows .exe artifact may be signed."
}

$required = @(
    "TKAUTO_SIGNTOOL_PATH",
    "TKAUTO_WINDOWS_CERTIFICATE_THUMBPRINT",
    "TKAUTO_WINDOWS_TIMESTAMP_URL"
)
foreach ($name in $required) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Required signing environment variable is missing: $name"
    }
}

$signTool = (Resolve-Path -LiteralPath ([Environment]::GetEnvironmentVariable("TKAUTO_SIGNTOOL_PATH"))).Path
$thumbprint = ([Environment]::GetEnvironmentVariable("TKAUTO_WINDOWS_CERTIFICATE_THUMBPRINT") -replace '\s', '').ToUpperInvariant()
if ($thumbprint -notmatch '^[0-9A-F]{40,64}$') {
    throw "The Authenticode certificate thumbprint is invalid."
}
$timestampUrl = $null
if (-not [Uri]::TryCreate([Environment]::GetEnvironmentVariable("TKAUTO_WINDOWS_TIMESTAMP_URL"), [UriKind]::Absolute, [ref]$timestampUrl) -or $timestampUrl.Scheme -ne "https") {
    throw "The Authenticode timestamp URL must use HTTPS."
}

& $signTool sign /sha1 $thumbprint /fd SHA256 /tr $timestampUrl.AbsoluteUri /td SHA256 $resolvedPath
if ($LASTEXITCODE -ne 0) { throw "signtool failed for $resolvedPath" }
& $signTool verify /pa /all /v $resolvedPath
if ($LASTEXITCODE -ne 0) { throw "signtool verification failed for $resolvedPath" }
$signature = Get-AuthenticodeSignature -LiteralPath $resolvedPath
if ($signature.Status -ne "Valid") {
    throw "Windows Authenticode verification failed ($($signature.Status))."
}
Write-Host "Authenticode signature verified: $resolvedPath"
