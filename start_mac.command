#!/bin/bash
set -e

cd "$(dirname "$0")"

PYTHON_VERSION="3.12.10"
PYTHON_INSTALLER_URL="https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"
PYTHON_INSTALLER_PATH="$HOME/Downloads/python-3.12.10-macos11.pkg"

show_message() {
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"$1\" message \"$2\""
  else
    echo "$1"
    echo "$2"
  fi
}

open_python_installer() {
  if command -v curl >/dev/null 2>&1; then
    curl -L "$PYTHON_INSTALLER_URL" -o "$PYTHON_INSTALLER_PATH"
    if command -v open >/dev/null 2>&1; then
      open "$PYTHON_INSTALLER_PATH"
    fi
  elif command -v open >/dev/null 2>&1; then
    open "$PYTHON_INSTALLER_URL"
  fi
}

find_python_with_tkinter() {
  candidates=(
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
    "/usr/local/bin/python3"
    "/opt/homebrew/bin/python3"
    "$(command -v python3 2>/dev/null || true)"
  )

  for candidate in "${candidates[@]}"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      if "$candidate" -c "import tkinter" >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done

  return 1
}

install_python_certificates() {
  local python_dir
  local cert_command

  python_dir="$(cd "$(dirname "$PYTHON_BIN")/.." && pwd)"
  cert_command="$python_dir/Install Certificates.command"

  if [ -f "$cert_command" ]; then
    /bin/bash "$cert_command" >/dev/null 2>&1 || true
  fi
}

PYTHON_BIN="$(find_python_with_tkinter || true)"

if [ -z "$PYTHON_BIN" ]; then
  open_python_installer
  show_message "Pythonの準備が必要です" "GUI部品つきのPython 3が見つかりませんでした。Python $PYTHON_VERSION のインストーラを開きます。インストールが終わったら、もう一度 start_mac.command を開いてください。"
  exit 1
fi

install_python_certificates

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

".venv/bin/python" -m pip install --upgrade pip
".venv/bin/python" -m pip install -r requirements.txt
".venv/bin/python" mask_video_gui.py
