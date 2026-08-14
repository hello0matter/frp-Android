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
pause

endlocal
