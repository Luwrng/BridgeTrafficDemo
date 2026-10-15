@echo off
setlocal
cd /d "%~dp0"
set "taskShell=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"
if not exist "%taskShell%" set "taskShell=powershell.exe"
"%taskShell%" -NoProfile -File "%~dp0tools\Start-CameraSystem.ps1"
if errorlevel 1 (
  echo Khong khoi dong duoc. Kiem tra Data\service-logs.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:5080/"
