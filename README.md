# ORDAL Auto Apply

ORDAL adalah dashboard auto-apply lowongan kerja untuk LinkedIn, LinkedIn Posts, dan JobStreet. Aplikasi ini menyimpan target lowongan, CV, riwayat apply, bank jawaban pertanyaan, serta mengirim pertanyaan apply ke dashboard dan Telegram.

## Fitur utama

- Login user dan penyimpanan credential platform secara terenkripsi.
- Upload beberapa CV dan pasangkan CV ke target posisi.
- Target pencarian berdasarkan posisi, lokasi, platform, tipe kerja, expected salary, dan cover letter.
- Bot apply untuk LinkedIn Jobs, LinkedIn Posts, dan JobStreet.
- Pertanyaan form apply muncul sebagai pop-up dashboard dan dikirim ke Telegram.
- Jawaban Telegram mengikuti tipe field:
  - dropdown dikirim sebagai tombol pilihan,
  - yes/no dikirim sebagai tombol,
  - angka wajib dibalas angka,
  - jawaban teks bisa dibalas langsung.
- Report Telegram otomatis setiap hari jam 16:00 WIB berisi lowongan yang berhasil di-apply, dikelompokkan per platform.
- User bisa minta report kapan saja dengan perintah `/report` atau `/report all` di Telegram.

## Struktur repo

```text
backend/   FastAPI, SQLite, bot workers, Telegram service
frontend/  React Vite dashboard
```

File runtime seperti `node_modules`, `dist`, `.venv`, database, cookie login, upload CV, screenshot debug, log, dan secret tidak disimpan di repo.

## Environment variable yang perlu disiapkan

### Backend

Copy file contoh:

```bash
cd backend
cp .env.example .env
```

Isi `backend/.env` di local atau server backend:

```env
APP_TIMEZONE=Asia/Jakarta
DB_PATH=./autoapply.db
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
JWT_SECRET=isi-random-panjang
ENCRYPTION_KEY=isi-fernet-key
TELEGRAM_BOT_TOKEN=token-dari-botfather
TELEGRAM_POLLING_ENABLED=1
TELEGRAM_REPORT_HOUR=16
TELEGRAM_REPORT_MINUTE=0
GEMINI_API_KEY=
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
EMAIL_SENDER=
EMAIL_APP_PASSWORD=
```

Generate `JWT_SECRET`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate `ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Untuk production, isi `CORS_ORIGINS` dengan domain frontend Vercel:

```env
CORS_ORIGINS=https://nama-app-kamu.vercel.app
```

### Frontend

Frontend memakai variable:

```env
VITE_API_BASE_URL=https://url-backend-kamu/api
```

Untuk Vercel, isi variable ini di:

```text
Vercel > Project > Settings > Environment Variables
```

Contoh PythonAnywhere:

```env
VITE_API_BASE_URL=https://username.pythonanywhere.com/api
```

## Setup backend local

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m playwright install chromium
python run.py
```

Tes backend:

```text
http://localhost:8000/
```

Jika berhasil, response:

```json
{"status":"ORDAL API v2 running"}
```

## Setup frontend local

```bash
cd frontend
npm install
npm run dev
```

Buka:

```text
http://localhost:5173
```

## Deploy frontend ke Vercel

Setting Vercel:

```text
Root Directory: frontend
Install Command: npm install
Build Command: npm run build
Output Directory: dist
```

Environment variable Vercel:

```env
VITE_API_BASE_URL=https://url-backend-kamu/api
```

## Deploy backend

Backend membutuhkan hosting Python yang support Playwright Chromium. Untuk demo, bisa dicoba di PythonAnywhere. Untuk pemakaian stabil, VPS lebih aman.

Contoh install di server:

```bash
git clone https://github.com/USERNAME/ORDAL.git
cd ORDAL/backend
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Edit `.env`, lalu jalankan sesuai hosting. Untuk PythonAnywhere, arahkan WSGI ke `backend/main.py` dan gunakan app FastAPI sebagai `application`.

Contoh WSGI PythonAnywhere:

```python
import os
import sys

project_home = '/home/USERNAME/ORDAL/backend'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from dotenv import load_dotenv
load_dotenv(os.path.join(project_home, '.env'))

from main import app as application
```

## Cara menghubungkan Telegram

1. Buat bot lewat `@BotFather`.
2. Isi `TELEGRAM_BOT_TOKEN` di backend `.env`.
3. Jalankan atau reload backend.
4. Buka dashboard ORDAL, masuk ke Settings, lalu copy command `/start KODE_LINK`.
5. Kirim command tersebut ke bot Telegram.
6. Setelah terhubung, pertanyaan apply akan masuk ke Telegram dan dashboard sekaligus.
7. Kirim `/report` untuk report hari ini.
8. Kirim `/report all` untuk report terbaru lintas hari.

## File yang tidak boleh masuk GitHub

Pastikan file ini tidak masuk repo:

```text
backend/.env
backend/secret.key
backend/encrypt.key
backend/*.db
backend/cookies/
backend/uploads/
backend/debug_screenshots/
frontend/node_modules/
frontend/dist/
frontend/.env.local
```

## Catatan penting

- Jangan taruh token Telegram, API key AI, password email, cookie login, database, atau CV user di GitHub.
- Variable frontend dengan awalan `VITE_` akan terlihat di browser, jadi jangan isi secret di frontend.
- Secret yang aman harus berada di server backend atau environment variable hosting.
