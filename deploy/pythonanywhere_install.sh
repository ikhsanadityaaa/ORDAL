#!/usr/bin/env bash
#
# ORDAL Auto Apply — Install script untuk PythonAnywhere
#
# CARA PAKAI:
# 1. Login ke PythonAnywhere dashboard
# 2. Buka halaman "Consoles" → "Bash" (new console)
# 3. Paste & jalankan perintah ini:
#
#    bash <(curl -fsSL https://raw.githubusercontent.com/ikhsanadityaaa/ORDAL/main/deploy/pythonanywhere_install.sh)
#
#    atau clone manual dulu, lalu:
#    bash deploy/pythonanywhere_install.sh
#
# SYARAT:
# - PythonAnywhere Hacker plan ($5/month) MINIMUM — butuh Always-on task + Playwright support
# - Free tier TIDAK SUPPORT Playwright Chromium
#
set -e

USERNAME=$(whoami)
INSTALL_DIR="/home/$USERNAME/ORDAL"
BRANCH="${1:-main}"

echo "========================================="
echo "  ORDAL Auto Apply — PythonAnywhere"
echo "========================================="
echo "User       : $USERNAME"
echo "Install dir: $INSTALL_DIR"
echo ""

# ── 1. Clone repo ──
echo "[1/6] Cloning repository..."
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git fetch --all
    git reset --hard "origin/$BRANCH"
else
    git clone --branch "$BRANCH" "https://github.com/ikhsanadityaaa/ORDAL.git" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 2. Virtualenv ──
echo "[2/6] Setting up virtualenv..."
python3 -m venv "$INSTALL_DIR/backend/.venv"
"$INSTALL_DIR/backend/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/backend/.venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt" -q
echo "  ✅ Dependencies installed"

# ── 3. Install Playwright Chromium ──
echo "[3/6] Installing Playwright Chromium..."
# PythonAnywhere butuh --with-deps dan path khusus
export PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.cache/ms-playwright"
"$INSTALL_DIR/backend/.venv/bin/python" -m playwright install chromium
echo "  ✅ Playwright Chromium terinstall"

# ── 4. Generate .env ──
echo "[4/6] Generating .env..."
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
PLAYWRIGHT_BROWSERS_PATH=$INSTALL_DIR/.cache/ms-playwright
EOF
    echo "  ✅ .env dibuat — EDIT dulu sebelum start!"
else
    echo "  .env sudah ada, skip."
fi
chmod 600 "$ENV_FILE"

# ── 5. Buat script always-on task ──
echo "[5/6] Creating always-on task script..."
cat > "$INSTALL_DIR/deploy/run_ordal.sh" <<EOF
#!/usr/bin/env bash
# Always-on task script untuk PythonAnywhere
# Jalankan ini di: Dashboard → Tasks → Always-on tasks
cd "$INSTALL_DIR/backend"
source "$INSTALL_DIR/backend/.venv/bin/activate"
export PLAYWRIGHT_BROWSERS_PATH="$INSTALL_DIR/.cache/ms-playwright"
exec python run.py
EOF
chmod +x "$INSTALL_DIR/deploy/run_ordal.sh"
echo "  ✅ run_ordal.sh dibuat"

# ── 6. Selesai ──
echo "[6/6] Done!"
echo ""
echo "========================================="
echo "  INSTALLASI SELESAI"
echo "========================================="
echo ""
echo "LANGKAH SELANJUTNYA:"
echo ""
echo "1. Edit file .env, isi token:"
echo "   nano $INSTALL_DIR/backend/.env"
echo "   - TELEGRAM_BOT_TOKEN (dari @BotFather)"
echo "   - GEMINI_API_KEY (dari https://aistudio.google.com/apikey)"
echo ""
echo "2. Setup Always-on Task di PythonAnywhere:"
echo "   - Buka Dashboard → Tasks → Always-on tasks"
echo "   - Klik 'Add a new task'"
echo "   - Command: bash $INSTALL_DIR/deploy/run_ordal.sh"
echo "   - Save"
echo ""
echo "3. Cek log di:"
echo "   - Dashboard → Tasks → klik task name"
echo "   - Atau: tail -f /var/log/ordal.log"
echo ""
echo "4. Buka bot Telegram Anda, kirim /start untuk mulai."
echo ""
echo "CATATAN PENTING:"
echo "- Butuh Hacker plan (\$5/month) minimum untuk Always-on task"
echo "- Free tier tidak support Playwright"
echo "- Restart task setiap update kode: Dashboard → Tasks → Restart"
echo ""
