[CmdletBinding()]
param(
    [string]$TemplatePath,
    [string]$OutputDirectory,
    [ValidateSet("arm64-v8a", "armeabi-v7a", "x86_64")][string]$Abi = "arm64-v8a",
    [string]$ServerAddr = "39.107.228.222",
    [ValidateRange(1, 65535)][int]$ServerPort = 7000,
    [ValidateRange(1, 65535)][int]$LocalPort = 5555,
    [ValidateRange(1, 65535)][int]$RemotePort = 6004,
    [string]$Token = $env:FRP_TOKEN,
    [string]$InstallBase = "/data/adb",
    [string]$ProfileName = "default",
    [string]$ProfilePath,
    [switch]$NoProfile,
    [bool]$IncludeAdbBootstrap = $true,
    [bool]$EnableFrpcLog = $true,
    [bool]$EnableFrpcSchedule = $false,
    [ValidateRange(10, 2147483)][int]$FrpcScheduleInterval = 3600,
    [string]$FrpcScheduleBody = ""
)
$ErrorActionPreference = "Stop"
$defaultScheduleBody = @'
child_pid=$(cat "$CHILD_PID" 2>/dev/null)
if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill "$child_pid" 2>/dev/null
    if [ "$LOG_ENABLED" = "1" ]; then
        printf '%s scheduled child restart\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
    fi
fi
'@.Trim()
$scheduleBodyFromProfile = $false
$root = Split-Path -Parent $PSScriptRoot
$defaultProfile = Join-Path $PSScriptRoot "profiles\active.json"
$ProfilePath = if ($ProfilePath) { $ProfilePath } elseif (!$NoProfile -and (Test-Path $defaultProfile)) { $defaultProfile } else { $null }
if ($ProfilePath -and (Test-Path -LiteralPath $ProfilePath)) {
    $profile = Get-Content -LiteralPath $ProfilePath -Raw | ConvertFrom-Json
    $get = {
        param([string]$name)
        $property = $profile.PSObject.Properties[$name]
        if ($null -ne $property) { return $property.Value }
        return $null
    }
    $value = & $get "serverAddr"; if ($null -ne $value -and $value) { $ServerAddr = [string]$value }
    $value = & $get "serverPort"; if ($null -ne $value -and $value) { $ServerPort = [int]$value }
    $value = & $get "localPort"; if ($null -ne $value -and $value) { $LocalPort = [int]$value }
    $value = & $get "remotePort"; if ($null -ne $value -and $value) { $RemotePort = [int]$value }
    $value = & $get "token"; if ($null -ne $value) { $Token = [string]$value }
    $value = & $get "installBase"; if ($null -ne $value -and $value) { $InstallBase = [string]$value }
    $value = & $get "includeAdbBootstrap"; if ($null -ne $value) { $IncludeAdbBootstrap = [bool]$value }
    $value = & $get "enableFrpcLog"; if ($null -ne $value) { $EnableFrpcLog = [bool]$value }
    $value = & $get "enableFrpcSchedule"; if ($null -ne $value) { $EnableFrpcSchedule = [bool]$value }
    $value = & $get "frpcScheduleInterval"; if ($null -ne $value -and $value) { $FrpcScheduleInterval = [int]$value }
    $property = $profile.PSObject.Properties["frpcScheduleBody"]
    if ($null -ne $property) { $FrpcScheduleBody = [string]$property.Value; $scheduleBodyFromProfile = $true }
    $value = & $get "profileName"; if ($null -ne $value -and $value) { $ProfileName = [string]$value }
}
$scheduleBody = if ($scheduleBodyFromProfile -or $FrpcScheduleBody) { $FrpcScheduleBody } else { $defaultScheduleBody }
$TemplatePath = if ($TemplatePath) { $TemplatePath } else { Join-Path $PSScriptRoot "frpc-service.sh" }
$OutputDirectory = if ($OutputDirectory) { $OutputDirectory } else { Join-Path $PSScriptRoot "personalized\service" }
$binary = Join-Path $root "app\src\main\jniLibs\$Abi\libfrpc.so"
if (!(Test-Path $binary)) { throw "FRPC binary not found: $binary" }
if ($InstallBase -notmatch '^/[A-Za-z0-9._/-]+$' -or $InstallBase -match '(^|/)\.\.(/|$)') { throw "Invalid InstallBase: $InstallBase" }

$adjectives = @("amber","autumn","brisk","bright","calm","clear","cool","crisp","gentle","golden","lucid","misty","modern","noble","quiet","rapid","silver","solar","steady","swift","tidy","vivid","warm","wild","wise","blue")
$nouns = @("beacon","bridge","canyon","cedar","cloud","coast","comet","creek","delta","field","flame","forest","garden","harbor","horizon","island","lantern","meadow","metro","mountain","ocean","orbit","path","peak","pine","river","signal","spring","stone","summit","valley","wave")
$bytes = New-Object byte[] 2
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    do {
        $rng.GetBytes($bytes)
        $name = "{0}-{1}" -f $adjectives[$bytes[0] % $adjectives.Count], $nouns[$bytes[1] % $nouns.Count]
        $dir = Join-Path $OutputDirectory $name
    } while (Test-Path $dir)
} finally { $rng.Dispose() }
$logName = "{0}-{1}" -f $adjectives[$bytes[0] % $adjectives.Count], $nouns[$bytes[1] % $nouns.Count]
if ($logName -eq $name) { $logName = "quiet-$($nouns[$bytes[0] % $nouns.Count])" }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$template = [IO.File]::ReadAllText((Resolve-Path $TemplatePath))
if (([regex]::Matches($template, "__INSTALL_NAME__").Count) -ne 1 -or
    ([regex]::Matches($template, "__INSTALL_ROOT__").Count) -ne 1 -or
    ([regex]::Matches($template, "__LOG_NAME__").Count) -ne 1 -or
    ([regex]::Matches($template, "__LOG_ENABLED__").Count) -ne 1 -or
    ([regex]::Matches($template, "__SCHEDULE_ENABLED__").Count) -ne 1 -or
    ([regex]::Matches($template, "__SCHEDULE_INTERVAL__").Count) -ne 1 -or
    ([regex]::Matches($template, "__SCHEDULE_BODY__").Count) -ne 1) { throw "Invalid template" }
$logEnabledValue = if ($EnableFrpcLog) { "1" } else { "0" }
$scheduleEnabledValue = if ($EnableFrpcSchedule) { "1" } else { "0" }
$script = $template.Replace("__INSTALL_NAME__", $name).Replace("__INSTALL_ROOT__", "$InstallBase/$name").Replace("__LOG_NAME__", $logName).Replace("__LOG_ENABLED__", $logEnabledValue).Replace("__SCHEDULE_ENABLED__", $scheduleEnabledValue).Replace("__SCHEDULE_INTERVAL__", [string]$FrpcScheduleInterval).Replace("__SCHEDULE_BODY__", $scheduleBody)
$adbScriptName = ""
if ($IncludeAdbBootstrap) {
    $adbTemp = Join-Path ([IO.Path]::GetTempPath()) "adb-service-$PID"
    New-Item -ItemType Directory -Force -Path $adbTemp | Out-Null
    try {
        $adbScriptPath = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "render-tcp-adb-preauthorized.ps1") -OutputDirectory $adbTemp
        if ($LASTEXITCODE -ne 0 -or !$adbScriptPath) { throw "ADB bootstrap script generation failed" }
        $adbScriptName = Split-Path -Leaf ([string]$adbScriptPath)
        Copy-Item -LiteralPath $adbScriptPath -Destination (Join-Path $dir $adbScriptName)
    } finally {
        Remove-Item -LiteralPath $adbTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
$script = $script.Replace("__ADB_SCRIPT__", $adbScriptName)
[IO.File]::WriteAllText((Join-Path $dir "$name.sh"), $script.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
function Q([string]$v) { if ($null -eq $v) { return "" }; $v.Replace("\", "\\").Replace('"', '\"') }
$ProfileName = $ProfileName.Replace("`r", "").Replace("`n", "")
$tokenLine = if ($Token) { "auth.token = `"$(Q $Token)`"`n" } else { "" }
$config = @"
# profileName: $ProfileName
serverAddr = "$(Q $ServerAddr)"
serverPort = $ServerPort
${tokenLine}loginFailExit = false

[log]
level = "info"
disablePrintColor = true

[[proxies]]
name = "$name"
type = "tcp"
localIP = "127.0.0.1"
localPort = $LocalPort
remotePort = $RemotePort
"@
[IO.File]::WriteAllText((Join-Path $dir "$name.toml"), $config.TrimStart(), [Text.UTF8Encoding]::new($false))
Copy-Item $binary (Join-Path $dir $name)
Write-Output (Join-Path $dir "$name.sh")
