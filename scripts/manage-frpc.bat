@echo off
setlocal
where pythonw.exe >nul 2>nul || (echo ERROR: Python 3 (pythonw.exe) is required.& exit /b 1)
rem 使用 pythonw 启动 Tk GUI，避免直接双击 BAT 时保留黑色控制台窗口。
start "" /b pythonw.exe "%~dp0device_manager.py"
endlocal
