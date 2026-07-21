#!/usr/bin/env bash
# Always-on task script untuk PythonAnywhere
# Jalankan ini di: Dashboard → Tasks → Always-on tasks → Add new task
# Command: bash /home/USERNAME/ORDAL/deploy/run_ordal.sh

USERNAME=$(whoami)
INSTALL_DIR="/home/$USERNAME/ORDAL"

cd "$INSTALL_DIR/backend"
source "$INSTALL_DIR/backend/.venv/bin/activate"
export PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.cache/ms-playwright"

# Jalankan uvicorn (FastAPI + scheduler + Telegram polling semua dalam 1 process)
exec python run.py
