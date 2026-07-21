#!/usr/bin/env bash
#
# ORDAL Auto Apply — Install script untuk VPS Linux (Ubuntu/Debian)
# Jalankan sebagai root atau dengan sudo:
#   bash deploy/install.sh
#
# Setelah selesai:
#   - Backend terinstall di /opt/ordal
#   - systemd service 'ordal' aktif & enabled
#   - Bot Telegram jalan di background 24/7
#
set -euo pipefail

# ── Config ──
INSTALL_DIR="/opt/ordal"
SERVICE_USER="ordal"
REPO_URL="${1:-https://github.com/ikhsanadityaaa/ORDAL.git}"
BRANCH="${2:-main}"

echo "========================================="
echo "  ORDAL Auto Apply — VPS Installer"
echo "========================================="
echo "Install dir : $INSTALL_DIR"
echo "Service user: $SERVICE_USER"
echo "Repo        : $REPO_URL"
echo ""

# ── 1. Install system dependencies ──
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    git curl wget \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 \
    libasound2t64 libasound2 2>/dev/null || true

# ── 2. Create service user ──
echo "[2/8] Creating service user '$SERVICE_USER'..."
if ! id -u "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --home-dir "/home/$SERVICE_USER" --shell /bin/bash "$SERVICE_USER"
fi

# ── 3. Clone repo ──
echo "[3/8] Cloning repository..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  Directory $INSTALL_DIR already exists. Pulling latest..."
    cd "$INSTALL_DIR"
    git fetch --all
    git reset --hard "origin/$BRANCH"
else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 4. Python virtualenv ──
echo "[4/8] Setting up Python virtualenv..."
python3 -m venv "$INSTALL_DIR/backend/.venv"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip -q
sudo -u "$SERVICE_USER" "$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt" -q

# ── 5. Install Playwright Chromium ──
echo "[5/8] Installing Playwright Chromium..."
sudo -u "$SERVICE_USER" "$INSTALL_DIR/backend/.venv/bin/python" -m playwright install chromium
sudo -u "$SERVICE_USER" "$INSTALL_DIR/backend/.venv/bin/python" -m playwright install-deps chromium 2>/dev/null || true

# ── 6. Generate .env if not exists ──
echo "[6/8] Generating .env..."
ENV_FILE="$INSTALL_DIR/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
    JWT_SECRET=$("$INSTALL_DIR/backend/.venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")
    ENCRYPTION_KEY=$("$INSTALL_DIR/backend/.venv/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    cat > "$ENV_FILE" <<EOF
APP_TIMEZONE=Asia/Jakarta
DB_PATH=./autoapply.db
CORS_ORIGINS=
JWT_SECRET=$JWT_SECRET
ENCRYPTION_KEY=$ENCRYPTION_KEY
TELEGRAM_BOT_TOKEN=
TELEGRAM_POLLING_ENABLED=1
TELEGRAM_REPORT_HOUR=16
TELEGRAM_REPORT_MINUTE=0
GEMINI_API_KEY=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
EMAIL_SENDER=
EMAIL_APP_PASSWORD=
LOGIN_TIMEOUT_MS=300000
EOF
    echo "  .env dibuat. EDIT $ENV_FILE dan isi TELEGRAM_BOT_TOKEN + GEMINI_API_KEY sebelum start!"
else
    echo "  .env sudah ada, skip."
fi
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ── 7. Install systemd service ──
echo "[7/8] Installing systemd service..."
cp "$INSTALL_DIR/deploy/ordal.service" /etc/systemd/system/ordal.service
systemctl daemon-reload
systemctl enable ordal

# ── 8. Selesai ──
echo "[8/8] Done!"
echo ""
echo "========================================="
echo "  INSTALLASI SELESAI"
echo "========================================="
echo ""
echo "LANGKAH SELANJUTNYA:"
echo ""
echo "1. Edit file .env dan isi token:"
echo "   nano $INSTALL_DIR/backend/.env"
echo "   - TELEGRAM_BOT_TOKEN (dari @BotFather)"
echo "   - GEMINI_API_KEY (dari https://aistudio.google.com/apikey)"
echo ""
echo "2. Jalankan service:"
echo "   systemctl start ordal"
echo ""
echo "3. Cek status:"
echo "   systemctl status ordal"
echo ""
echo "4. Cek log:"
echo "   journalctl -u ordal -f"
echo ""
echo "5. Buka bot Telegram Anda, kirim /register untuk daftar akun."
echo ""
echo " Untuk update kode berikutnya:"
echo "   cd $INSTALL_DIR && git pull && systemctl restart ordal"
echo ""
