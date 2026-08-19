@echo off
setlocal
set "PYTHONW="
for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined PYTHONW set "PYTHONW=%%I"
if not defined PYTHONW (
    echo ERROR: Python 3 with pythonw.exe is required.
    pause
    exit /b 1
)
start "" "%PYTHONW%" "%~dp0device_manager.py"
exit /b 0
