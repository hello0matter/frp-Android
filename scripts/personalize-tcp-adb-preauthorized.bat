@echo off
setlocal

set "RENDERER=%~dp0render-tcp-adb-preauthorized.ps1"
set "SCRIPT="

if not exist "%RENDERER%" (
    echo Renderer not found: %RENDERER%
    exit /b 1
)

for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RENDERER%"`) do set "SCRIPT=%%I"
if not defined SCRIPT (
    echo Failed to generate a personalized shell script.
    exit /b 1
)

echo.
echo Personalized script created:
echo %SCRIPT%
echo.
echo Recommended:
echo 1. Copy this file to any location readable by root on the phone.
echo 2. Run it once with: su -c sh /path/to/script.sh
echo 3. It will install itself into /data/adb/service.d and set permissions automatically.
echo.
echo Alternative for a recovery image or flash package:
echo Copy it directly into /data/adb/service.d, then set owner root:root and mode 0700.
echo.
pause

endlocal
