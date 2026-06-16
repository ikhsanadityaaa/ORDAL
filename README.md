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
- User bisa minta report kapan saja dengan perintah `/report` di Telegram.

## Struktur repo ringkas

```text
backend/   FastAPI, SQLite, bot workers, Telegram service
frontend/  React Vite dashboard
```

File runtime seperti `node_modules`, `dist`, `.venv`, database, cookie login, upload CV, screenshot debug, log, dan secret tidak disimpan di repo.

## Setup backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m playwright install chromium
python run.py
```

Isi minimal `.env`:

```env
JWT_SECRET=isi-random-panjang
TELEGRAM_BOT_TOKEN=token-dari-botfather
APP_TIMEZONE=Asia/Jakarta
```

## Setup frontend

```bash
cd frontend
npm install
npm run dev
```

Untuk production:

```bash
npm run build
```

## Cara menghubungkan Telegram

1. Buat bot lewat BotFather dan isi `TELEGRAM_BOT_TOKEN` di backend `.env`.
2. Jalankan backend.
3. Buka dashboard ORDAL, masuk ke Settings, lalu copy command `/start KODE_LINK`.
4. Kirim command tersebut ke bot Telegram.
5. Setelah terhubung, pertanyaan apply akan masuk ke Telegram dan dashboard sekaligus.
6. Kirim `/report` ke Telegram untuk melihat report lamaran hari ini. Kirim `/report all` untuk melihat report terbaru lintas hari.

## Catatan deployment

- Jangan upload `backend/cookies`, `backend/uploads`, `backend/*.db`, `backend/secret.key`, atau file `.env` ke GitHub.
- Untuk Vercel, deploy folder `frontend`.
- Untuk backend, gunakan hosting Python yang support Playwright Chromium. Jika memakai PythonAnywhere free, beberapa fitur browser automation bisa terbatas.
