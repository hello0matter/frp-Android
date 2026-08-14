[CmdletBinding()]
param(
    [string]$TemplatePath,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($TemplatePath)) {
    $TemplatePath = Join-Path $PSScriptRoot '99-tcp-adb-preauthorized.sh'
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $env:TEMP 'frp-android-adb-scripts'
}

$publicKeyPath = Join-Path $env:USERPROFILE '.android\adbkey.pub'
if (!(Test-Path -LiteralPath $publicKeyPath)) {
    throw "ADB public key was not found: $publicKeyPath"
}
if (!(Test-Path -LiteralPath $TemplatePath)) {
    throw "Shell template was not found: $TemplatePath"
}

$publicKey = (Get-Content -LiteralPath $publicKeyPath -Raw).Trim()
if ($publicKey -notmatch '^Q[A-Za-z0-9+/=]+(?:\s+.*)?$' -or $publicKey.Contains("`n")) {
    throw "Invalid ADB public key format: $publicKeyPath"
}

$randomBytes = New-Object byte[] 8
$randomNumberGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomNumberGenerator.GetBytes($randomBytes)
} finally {
    $randomNumberGenerator.Dispose()
}
$installToken = -join ($randomBytes | ForEach-Object { $_.ToString('x2') })

$template = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $TemplatePath))
if ([regex]::Matches($template, [regex]::Escape('__ADB_PUBLIC_KEY__')).Count -ne 1) {
    throw 'The shell template must contain exactly one public-key placeholder.'
}
if ([regex]::Matches($template, [regex]::Escape('__INSTALL_TOKEN__')).Count -ne 1) {
    throw 'The shell template must contain exactly one install-token placeholder.'
}

$rendered = $template.Replace('__ADB_PUBLIC_KEY__', $publicKey)
$rendered = $rendered.Replace('__INSTALL_TOKEN__', $installToken)
$rendered = $rendered.Replace("`r`n", "`n").Replace("`r", "`n")

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$outputPath = Join-Path $OutputDirectory "99-tcp-adb-preauthorized-$installToken.sh"
[IO.File]::WriteAllText($outputPath, $rendered, [Text.UTF8Encoding]::new($false))

Write-Output $outputPath
