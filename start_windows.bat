@echo off
setlocal
cd /d "%~dp0"

set PYTHON_VERSION=3.12.10
set PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
set PYTHON_INSTALLER_PATH=%TEMP%\python-3.12.10-amd64.exe

py -3 -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo Python 3 が見つかりません。Python %PYTHON_VERSION% をダウンロードして起動します。
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%PYTHON_INSTALLER_URL%' -OutFile '%PYTHON_INSTALLER_PATH%'; Start-Process -FilePath '%PYTHON_INSTALLER_PATH%' -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_pip=1' -Wait"
  echo インストールが終わりました。もう一度 start_windows.bat を開いてください。
  pause
  exit /b 0
)

py -3 -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo Tkinter が見つかりません。Python %PYTHON_VERSION% をダウンロードして起動します。
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%PYTHON_INSTALLER_URL%' -OutFile '%PYTHON_INSTALLER_PATH%'; Start-Process -FilePath '%PYTHON_INSTALLER_PATH%' -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_pip=1' -Wait"
  echo インストールが終わりました。もう一度 start_windows.bat を開いてください。
  pause
  exit /b 0
)

if not exist ".venv" (
  py -3 -m venv .venv
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo ライブラリ準備に失敗しました。
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ライブラリ準備に失敗しました。
  pause
  exit /b 1
)

".venv\Scripts\python.exe" mask_video_gui.py
pause