@echo off
setlocal

set "RENDERER=%~dp0render-tcp-adb-preauthorized.ps1"
set "SCRIPT="

where adb >nul 2>nul
if errorlevel 1 (
    echo adb was not found in PATH.
    exit /b 1
)

if not exist "%RENDERER%" (
    echo Renderer not found: %RENDERER%
    exit /b 1
)

for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RENDERER%" -OutputDirectory "%TEMP%\frp-android-adb-scripts"`) do set "SCRIPT=%%I"
if not defined SCRIPT (
    echo Failed to generate a personalized shell script.
    exit /b 1
)

for %%F in ("%SCRIPT%") do (
    set "REMOTE=/data/local/tmp/%%~nxF"
    set "SERVICE=/data/adb/service.d/%%~nxF"
)
echo Generated: %SCRIPT%

adb push "%SCRIPT%" "%REMOTE%"
if errorlevel 1 exit /b 1

echo Installing and starting TCP ADB...
adb shell su -c "sh %REMOTE%"

echo.
echo Installation command completed. adbd may disconnect briefly while restarting.
echo Boot script: %SERVICE%
echo Log: /data/adb/tcp-adb-preauthorized.log

endlocal
