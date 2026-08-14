@echo off
setlocal

set "SCRIPT=%~dp099-tcp-adb-preauthorized.sh"
set "REMOTE=/data/local/tmp/99-tcp-adb-preauthorized.sh"

where adb >nul 2>nul
if errorlevel 1 (
    echo adb was not found in PATH.
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo Script not found: %SCRIPT%
    exit /b 1
)

adb push "%SCRIPT%" "%REMOTE%"
if errorlevel 1 exit /b 1

echo Installing and starting TCP ADB...
adb shell su -c "sh %REMOTE%"

echo.
echo Installation command completed. adbd may disconnect briefly while restarting.
echo Boot script: /data/adb/service.d/99-tcp-adb.sh
echo Log: /data/adb/tcp-adb-preauthorized.log

endlocal
