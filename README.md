# ORDAL Auto Apply

Bot Telegram untuk auto-apply lowongan kerja di **LinkedIn Jobs**, **LinkedIn Posts**, dan **JobStreet**. Setiap hari, bot otomatis mencari dan melamar lowongan yang cocok dengan target Anda. Pertanyaan form apply dijawab otomatis dengan AI (Gemini) atau dari bank jawaban yang sudah Anda simpan.

## Konsep

**Telegram-only** — semua operasi dilakukan via bot Telegram, tidak ada dashboard web. Backend FastAPI berjalan di VPS 24/7, scheduler otomatis memulai apply session setiap hari sesuai jadwal yang Anda atur.

```
┌──────────────┐     ┌──────────────────────────────────┐
│  Telegram    │◄───►│  FastAPI Backend (VPS)           │
│  Bot         │     │  ├─ Telegram polling             │
│  (user UI)   │     │  ├─ Auto-apply scheduler         │
└──────────────┘     │  ├─ Daily report scheduler       │
                     │  ├─ Playwright bots (LI/JS/Posts)│
                     │  ├─ Gemini AI (form answer)      │
                     │  └─ SQLite database              │
                     └──────────────────────────────────┘
```

## Fitur Utama

- **Auto-apply harian**: set jam & hari, bot otomatis apply tiap hari.
- **Telegram sebagai UI**: register, login, upload CV, upload cookie, atur target, atur jadwal — semua via command Telegram.
- **AI fallback**: pertanyaan form baru dijawab Gemini otomatis, notifikasi dikirim ke Telegram untuk review/edit.
- **3 platform**: LinkedIn Easy Apply, LinkedIn Posts (email recruiter), JobStreet.
- **Question bank**: jawaban tersimpan & dipakai ulang untuk pertanyaan serupa.
- **Cookie expiry detection**: kalau cookie expired, platform di-skip & user dinotifikasi.
- **Daily report**: laporan lamaran terkirim otomatis setiap hari jam 16:00 WIB.
- **Security**: password email dienkripsi (Fernet/AES), password user di-hash (bcrypt).

## Quick Start (VPS Linux)

```bash
# 1. Clone repo ke VPS
git clone https://github.com/ikhsanadityaaa/ORDAL.git
cd ORDAL

# 2. Jalankan install script (butuh sudo/root)
sudo bash deploy/install.sh

# 3. Edit .env, isi token
sudo nano /opt/ordal/backend/.env
# Isi: TELEGRAM_BOT_TOKEN, GEMINI_API_KEY

# 4. Start service
sudo systemctl start ordal

# 5. Cek status
sudo systemctl status ordal
sudo journalctl -u ordal -f
```

## Quick Start (PythonAnywhere)

PythonAnywhere cocok untuk yang tidak mau manage VPS. **Butuh Hacker plan ($5/bulan) minimum** — free tier tidak support Playwright Chromium & tidak ada Always-on task.

### Step-by-step PythonAnywhere

1. **Login PythonAnywhere** → buka tab **Bash console** (Dashboard → Consoles → Bash)

2. **Clone repo + install dependencies:**
   ```bash
   git clone https://github.com/ikhsanadityaaa/ORDAL.git
   cd ORDAL
   bash deploy/pythonanywhere_install.sh
   ```

3. **Edit `.env`, isi token:**
   ```bash
   nano /home/$USER/ORDAL/backend/.env
   ```
   Isi 2 baris wajib:
   ```env
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...   # dari @BotFather
   GEMINI_API_KEY=AIzaSy...                # dari https://aistudio.google.com/apikey
   ```
   Save: `Ctrl+O`, `Enter`, `Ctrl+X`

4. **Setup Always-on task** (agar bot jalan 24/7):
   - Dashboard → **Tasks** → **Always-on tasks** tab
   - Klik **"Add a new task"**
   - **Command:** `bash /home/USERNAME/ORDAL/deploy/run_ordal.sh`
     (ganti `USERNAME` dengan username PythonAnywhere Anda)
   - Klik **Create**

5. **Cek log:**
   - Dashboard → Tasks → klik nama task → lihat log
   - Atau di Bash console: `tail -f /home/USERNAME/ORDAL/backend/ordal_debug.log`

6. **Buka bot Telegram Anda** → kirim `/start` → lanjut `/register`

### Update kode di PythonAnywhere

```bash
cd ~/ORDAL
git pull origin main
# Restart Always-on task di Dashboard → Tasks → Restart
```

### Kenapa Hacker plan ($5/bulan) minimum?

- **Playwright Chromium** butuh binary yang tidak tersedia di free tier
- **Always-on task** (untuk jalan 24/7) hanya ada di paid plan
- Free tier console **timeout setelah idle** — scheduler & Telegram polling akan mati

### Troubleshooting PythonAnywhere

- **Playwright error "Executable doesn't exist"**: jalankan ulang `python -m playwright install chromium` di virtualenv
- **Bot mati setelah beberapa jam**: pastikan pakai Always-on task, bukan console biasa
- **Port 8000 tidak bisa diakses**: tidak masalah, bot pakai Telegram polling (outbound HTTPS), tidak butuh inbound port

## Setup Telegram Bot

1. Buka Telegram, cari `@BotFather`.
2. Kirim `/newbot`, ikuti instruksi untuk buat bot baru.
3. Copy **Bot Token** yang diberikan.
4. Isi token di `.env` (`TELEGRAM_BOT_TOKEN`).
5. Restart service: `sudo systemctl restart ordal`.
6. Buka bot Anda di Telegram, kirim `/start`.

## Command Telegram Lengkap

### Akun
| Command | Deskripsi |
|---------|-----------|
| `/register nama\|email\|password` | Daftar akun baru |
| `/login email\|password` | Hubungkan akun existing |
| `/logout` | Putuskan koneksi Telegram |
| `/profile` | Lihat info akun |

### Target Lowongan
| Command | Deskripsi |
|---------|-----------|
| `/targets` | Daftar target aktif |
| `/target_add` | Tambah target (interaktif) |
| `/target_add posisi\|lokasi\|platform` | Tambah target (inline) |
| `/target_del <id>` | Hapus target |

Platform: `linkedin`, `linkedin_posts`, `jobstreet`, `both`, `all`

### CV
| Command | Deskripsi |
|---------|-----------|
| `/cv` | Daftar CV tersimpan |
| `/cv_add` | Upload CV (kirim file PDF setelah command) |
| `/cv_del <id>` | Hapus CV |

### Credentials (Cookie Platform)
| Command | Deskripsi |
|---------|-----------|
| `/credentials` | Status login per platform |
| `/cookie linkedin` | Upload cookie LinkedIn (kirim file JSON setelah command) |
| `/cookie jobstreet` | Upload cookie JobStreet |

**Cara export cookie:**
1. Install extension **Cookie Editor** di browser Chrome/Firefox.
2. Login ke LinkedIn/JobStreet di browser.
3. Buka extension → **Export** → format **JSON**.
4. Save file, kirim ke bot setelah command `/cookie <platform>`.

### Preferences
| Command | Deskripsi |
|---------|-----------|
| `/prefs` | Lihat semua preferences |
| `/pref_set salary 7000000` | Set expected salary |
| `/pref_set join "1 month"` | Set available join |
| `/pref_set headless on` | Mode headless (tanpa window browser) |
| `/pref_set email_test off` | Testing email mode |

### Auto-Apply
| Command | Deskripsi |
|---------|-----------|
| `/autoapply` | Lihat status & jadwal |
| `/autoapply on` | Aktifkan auto-apply |
| `/autoapply off` | Nonaktifkan |
| `/autoapply time 09:00` | Set jam auto-apply |
| `/autoapply days mon,tue,wed,thu,fri` | Set hari aktif |

### Session
| Command | Deskripsi |
|---------|-----------|
| `/apply` | Mulai apply sekarang (manual trigger) |
| `/stop` | Hentikan session aktif |
| `/status` | Cek progress session |

### Question Bank
| Command | Deskripsi |
|---------|-----------|
| `/questions` | Daftar 20 jawaban terbaru |
| `/question_edit <id> <jawaban>` | Edit jawaban |

### Report
| Command | Deskripsi |
|---------|-----------|
| `/report` | Lamaran hari ini |
| `/report all` | Semua lamaran |

### Lainnya
| Command | Deskripsi |
|---------|-----------|
| `/help` | Tampilkan bantuan |

## Alur Penggunaan Baru

```
1. /register Budi|budi@email.com|rahasia123
2. /cv_add → kirim file PDF CV
3. /cookie linkedin → kirim file JSON cookie
4. /cookie jobstreet → kirim file JSON cookie
5. /target_add → ikuti prompt interaktif
6. /pref_set salary 7000000
7. /pref_set join "1 month"
8. /autoapply time 09:00
9. /autoapply days mon,tue,wed,thu,fri
10. /autoapply on
```

Setelah ini, bot akan otomatis apply setiap hari jam 09:00 WIB, Senin-Jumat. Anda akan dapat notifikasi:
- 🚀 **Start**: "Auto-apply dimulai, X targets..."
- 🤖 **AI answer**: "AI menjawab pertanyaan baru: Q... A..."
- ✅ **End**: "Auto-apply selesai: X applied, Y skipped, Z failed"
- ⚠️ **Cookie expired**: "Cookie LinkedIn expired, silakan upload baru"
- 📊 **Daily report** (16:00): "Report lamaran hari ini..."

## Architecture

### Background Tasks (di-start saat FastAPI startup)

1. **Telegram polling** (`telegram_service.poll_telegram_updates`)
   - Long-polling Telegram API setiap 25 detik
   - Handle incoming messages, callbacks, dan file uploads

2. **Auto-apply scheduler** (`auto_apply_scheduler.auto_apply_scheduler_loop`)
   - Cek setiap 60 detik apakah ada user yang perlu di-auto-apply
   - Match jam/menit dengan preference user
   - Start session dengan `source='auto'`, `headless_override=True`

3. **Daily report scheduler** (`telegram_service.daily_report_scheduler`)
   - Kirim report harian jam 16:00 WIB ke semua user

### Session Flow

```
User /autoapply on → scheduler cek jadwal → start_session_for_user(source='auto')
    → set auto_mode (answer_helper pakai Gemini, skip blocking prompt)
    → run LinkedInBot → run LinkedInPostsBot → run JobStreetBot
    → kirim notifikasi start ke Telegram
    → saat selesai, kirim notifikasi end + summary
    → clear auto_mode
```

### AI Fallback untuk Pertanyaan Form

Saat auto-apply menemukan pertanyaan yang tidak ada di question bank:

1. Cek question bank (similarity match)
2. Cek preferences (salary, join date)
3. Cek CV (experience years)
4. **Jika tidak ketemu**: Gemini AI menjawab → simpan ke question_bank (source='ai') → kirim notifikasi Telegram "AI menjawab: Q... A..."
5. User bisa edit nanti via `/question_edit <id> <jawaban baru>`

### Cookie Expiry Handling

1. Bot deteksi redirect ke login page → emit `cookie_expired` event
2. Session manager catch → update `user_credentials.cookie_valid=0` + `last_cookie_warning_at=now`
3. Platform di-skip, lanjut ke platform berikutnya
4. Notifikasi dikirim di end-of-session summary
5. Next day, scheduler cek `cookie_valid` — jika 0, skip platform tersebut

## Environment Variables

Lihat `backend/.env.example` untuk daftar lengkap. Yang penting:

| Variable | Wajib | Deskripsi |
|----------|-------|-----------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Token dari @BotFather |
| `GEMINI_API_KEY` | ✅ | API key dari Google AI Studio |
| `JWT_SECRET` | ✅ | Secret untuk JWT (auto-generated jika kosong) |
| `ENCRYPTION_KEY` | ✅ | Fernet key untuk encrypt email password |
| `APP_TIMEZONE` | ❌ | Default: Asia/Jakarta |
| `TELEGRAM_REPORT_HOUR` | ❌ | Default: 16 |

## Development Setup (Local)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
# Edit .env, isi token
ORDAL_DEV=1 python run.py
```

Untuk development, set `ORDAL_DEV=1` agar uvicorn reload otomatis saat kode berubah. **Jangan pakai di production** karena reload membunuh scheduler & session yang sedang berjalan.

## Update ke Versi Baru

```bash
cd /opt/ordal
git pull origin main
sudo systemctl restart ordal
sudo journalctl -u ordal -f
```

## File yang Tidak Boleh Masuk GitHub

```text
backend/.env
backend/secret.key
backend/encrypt.key
backend/*.db
backend/cookies/
backend/uploads/
backend/debug_screenshots/
backend/ordal_debug.log
```

## Troubleshooting

**Bot tidak merespon command**
- Cek `TELEGRAM_BOT_TOKEN` sudah benar di `.env`
- Cek `journalctl -u ordal -f` untuk error
- Pastikan tidak ada bot lain pakai token yang sama

**Auto-apply tidak jalan**
- Cek `/autoapply` status sudah ON
- Cek jam & hari sudah benar
- Cek `/credentials` — minimal 1 platform harus valid
- Cek `/targets` — minimal 1 target aktif
- Cek `journalctl -u ordal` untuk error scheduler

**Cookie expired terus**
- Login ulang ke LinkedIn/JobStreet di browser
- Export cookie baru via Cookie Editor extension
- Kirim `/cookie linkedin` → upload file JSON baru

**Playwright crash**
- Pastikan `python -m playwright install chromium` sudah dijalankan
- Cek memory VPS (minimal 2GB untuk Playwright)
- Set `/pref_set headless on` untuk mode headless

## Catatan Keamanan

- Password user di-hash dengan bcrypt.
- Password email SMTP dienkripsi dengan Fernet (AES-128).
- Cookie platform disimpan sebagai file JSON di server (tidak di DB).
- JWT token 30 hari expiry.
- Jangan commit `.env`, `secret.key`, `encrypt.key`, `*.db`, `cookies/`, `uploads/` ke GitHub.
