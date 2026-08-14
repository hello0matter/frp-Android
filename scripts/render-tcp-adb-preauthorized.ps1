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
    $OutputDirectory = Join-Path $PSScriptRoot 'personalized'
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

$adjectives = @(
    'amber', 'autumn', 'brisk', 'bright', 'calm', 'clear', 'cool', 'crisp',
    'gentle', 'golden', 'grand', 'hidden', 'lively', 'lucid', 'misty', 'modern',
    'noble', 'quiet', 'rapid', 'silver', 'solar', 'steady', 'still', 'swift',
    'tidy', 'vivid', 'warm', 'wild', 'wise', 'young', 'zenith', 'blue'
)
$nouns = @(
    'beacon', 'bridge', 'canyon', 'cedar', 'cloud', 'coast', 'comet', 'creek',
    'delta', 'field', 'flame', 'forest', 'garden', 'harbor', 'horizon', 'island',
    'lantern', 'meadow', 'metro', 'mountain', 'ocean', 'orbit', 'path', 'peak',
    'pine', 'river', 'signal', 'spring', 'stone', 'summit', 'valley', 'wave'
)

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$randomBytes = New-Object byte[] 2
$randomNumberGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    do {
        $randomNumberGenerator.GetBytes($randomBytes)
        $installName = '{0}-{1}' -f (
            $adjectives[$randomBytes[0] % $adjectives.Count],
            $nouns[$randomBytes[1] % $nouns.Count]
        )
        $outputPath = Join-Path $OutputDirectory "$installName.sh"
    } while (Test-Path -LiteralPath $outputPath)
} finally {
    $randomNumberGenerator.Dispose()
}

$template = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $TemplatePath))
if ([regex]::Matches($template, [regex]::Escape('__ADB_PUBLIC_KEY__')).Count -ne 1) {
    throw 'The shell template must contain exactly one public-key placeholder.'
}
if ([regex]::Matches($template, [regex]::Escape('__INSTALL_NAME__')).Count -ne 1) {
    throw 'The shell template must contain exactly one install-name placeholder.'
}

$rendered = $template.Replace('__ADB_PUBLIC_KEY__', $publicKey)
$rendered = $rendered.Replace('__INSTALL_NAME__', $installName)
$rendered = $rendered.Replace("`r`n", "`n").Replace("`r", "`n")

[IO.File]::WriteAllText($outputPath, $rendered, [Text.UTF8Encoding]::new($false))

Write-Output $outputPath
