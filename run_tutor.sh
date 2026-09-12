#!/usr/bin/env bash
# Launcher for the Python tutor desktop app.
# Checks tkinter + requests, installs what it can, then runs the GUI.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

echo_check() { printf "  [%s] %s\n" "$1" "$2"; }
ok()   { echo_check "OK"       "$1"; }
miss() { echo_check "MISSING"  "$1"; }

echo "python == $("$PY" --version)"

# 1) Python itself
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "error: '$PY' not found. Install Python 3, e.g.  sudo apt install python3"
    exit 1
fi

# 2) tkinter
if "$PY" -c "import tkinter" >/dev/null 2>&1; then
    ok "tkinter"
else
    miss "tkinter"
    echo "  -> Your system Python lacks tkinter. Install it with:"
    echo "       sudo apt install python3-tk"
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        read -r -p "     Run that now with sudo? [y/N] " ans
        if [[ "${ans,,}" == y* ]]; then
            sudo apt install -y python3-tk
        fi
    fi
    # re-check
    if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
        echo "error: tkinter still unavailable. Install python3-tk and re-run."
        exit 1
    fi
    ok "tkinter"
fi

# 3) requests
if "$PY" -c "import requests" >/dev/null 2>&1; then
    ok "requests ($($PY -c 'import requests; print(requests.__version__)'))"
else
    miss "requests"
    echo "  -> Installing requests:"
    "$PY" -m pip install --user requests || \
        "$PY" -m pip install --break-system-packages --user requests || {
            echo "error: could not install requests. Try:  $PY -m pip install requests"
            exit 1
        }
    ok "requests"
fi

# 4) API keys
if ! grep -q '^VITE_GEMINI_API_KEY=.' .env 2>/dev/null && \
   ! grep -q '^VITE_OPENCODE_API_KEY=.' .env 2>/dev/null; then
    echo "  [WARN] .env with API keys not found. The chat will fall back / fail until keys are set."
fi

echo "Starting tutor..."
exec "$PY" python_tutor_gui.py