"""
ORDAL Telegram Bot Service — Telegram-first UI.

Semua operasi yang sebelumnya via React dashboard sekarang via command Telegram:
- Auth: /register, /login, /logout, /profile
- Target: /targets, /target_add, /target_del
- CV: /cv (upload via reply document), /cv_del
- Preferences: /prefs, /pref_set
- Auto-apply: /autoapply (on/off/time/days)
- Session: /apply, /stop, /status
- Credentials: /credentials, /cookie (upload via reply document)
- Question bank: /questions, /question_edit
- Report: /report, /report all
- Help: /help
"""
import asyncio
import html
import os
import re
import secrets
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from database import get_db

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_POLLING_ENABLED = os.getenv("TELEGRAM_POLLING_ENABLED", "1").strip() != "0"
TELEGRAM_REPORT_HOUR = int(os.getenv("TELEGRAM_REPORT_HOUR", "16"))
TELEGRAM_REPORT_MINUTE = int(os.getenv("TELEGRAM_REPORT_MINUTE", "0"))
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Jakarta"))

_update_offset = 0
_polling_task: asyncio.Task | None = None
_report_task: asyncio.Task | None = None
_last_report_date: date | None = None

# Conversation state for multi-step flows (e.g. /target_add, /cookie upload)
# Key: chat_id -> {"step": str, "data": dict}
_conversation_state: dict[str, dict] = {}


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def telegram_available() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)


def ensure_telegram_columns(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_users (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT UNIQUE,
            link_code TEXT UNIQUE,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )


def get_or_create_link_code(user_id: int) -> str:
    db = get_db()
    row = db.execute("SELECT link_code FROM telegram_users WHERE user_id=?", (user_id,)).fetchone()
    if row and row["link_code"]:
        db.close()
        return row["link_code"]
    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10].upper()
    db.execute(
        """
        INSERT INTO telegram_users (user_id, link_code, enabled)
        VALUES (?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET link_code=excluded.link_code, updated_at=datetime('now')
        """,
        (user_id, code),
    )
    db.commit()
    db.close()
    return code


def get_user_telegram(user_id: int) -> dict[str, Any]:
    db = get_db()
    row = db.execute(
        "SELECT user_id, chat_id, link_code, enabled, updated_at FROM telegram_users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    db.close()
    return dict(row) if row else {"user_id": user_id, "chat_id": None, "link_code": None, "enabled": 0}


def _get_user_by_chat(chat_id: str) -> dict | None:
    """Ambil user info dari chat_id. Return None jika belum terhubung."""
    db = get_db()
    try:
        row = db.execute(
            """
            SELECT u.id, u.email, u.name, t.enabled
            FROM telegram_users t
            JOIN users u ON u.id = t.user_id
            WHERE t.chat_id = ? AND t.enabled = 1
            """,
            (chat_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


# ── Telegram API helpers ───────────────────────────────────────────────

async def send_telegram_message(chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    if not telegram_available() or not chat_id:
        return False
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.post(_api_url("sendMessage"), json=payload)
            return res.status_code == 200
    except Exception:
        return False


async def _download_telegram_file(file_id: str) -> bytes | None:
    """Download file dari Telegram (untuk CV PDF upload & cookie JSON upload)."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.get(_api_url("getFile"), params={"file_id": file_id})
            payload = res.json()
            if not payload.get("ok"):
                return None
            file_path = payload["result"]["file_path"]
            dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            res2 = await client.get(dl_url)
            if res2.status_code == 200:
                return res2.content
    except Exception:
        return None
    return None


# ── Question prompt helpers (existing, kept for backward compat) ───────

def parse_prompt_options(question: str) -> list[str]:
    match = re.search(r"options:\s*([\s\S]*)$", question or "", re.I)
    if not match:
        return []
    seen = set()
    options = []
    for raw in re.split(r";|\n|,", match.group(1)):
        item = raw.strip()
        if not item or item.lower() in {"select an option", "select", "pilih", "choose", "-", "--", "none"}:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append(item[:80])
    return options[:20]


def clean_question_text(question: str) -> str:
    return re.sub(r"\s*options:\s*[\s\S]*$", "", question or "", flags=re.I).strip() or (question or "").strip()


def prompt_kind(field_type: str, question: str) -> str:
    field = (field_type or "").lower()
    text = f"{question or ''} {field}".lower()
    if field == "number" or any(k in text for k in ("gaji", "salary", "umur", "usia", "tahun", "bulan", "year", "month", "nominal", "amount")):
        return "number"
    if field == "yes_no":
        return "yes_no"
    if field == "dropdown" and parse_prompt_options(question):
        return "dropdown"
    if field == "textarea":
        return "textarea"
    return "text"


def validate_answer(answer: str, field_type: str, question: str, options: list[str] | None = None) -> tuple[bool, str]:
    answer = (answer or "").strip()
    kind = prompt_kind(field_type, question)
    if not answer:
        return False, "Jawaban tidak boleh kosong."
    if kind == "number":
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", answer):
            return False, "Jawaban harus angka saja. Contoh: 7000000"
    if kind == "yes_no" and answer.lower() not in {"yes", "no", "ya", "tidak"}:
        return False, "Jawaban harus Yes atau No."
    if kind == "dropdown" and options:
        allowed = {opt.lower(): opt for opt in options}
        if answer.lower() not in allowed:
            return False, "Pilih salah satu opsi yang tersedia di tombol Telegram."
        return True, allowed[answer.lower()]
    return True, answer


async def send_question_to_telegram(user_id: int, prompt_event: dict) -> None:
    config = get_user_telegram(user_id)
    chat_id = config.get("chat_id")
    if not (chat_id and config.get("enabled")):
        return

    question = prompt_event.get("question") or ""
    options = prompt_event.get("options") or parse_prompt_options(question)
    field_type = prompt_event.get("field_type") or "text"
    kind = prompt_event.get("answer_mode") or prompt_kind(field_type, question)
    platform = prompt_event.get("platform") or "platform"
    job_title = prompt_event.get("job_title") or "Lamaran kerja"
    prompt_id = prompt_event.get("prompt_id") or ""

    text = (
        "<b>ORDAL butuh jawaban</b>\n"
        f"Platform: <b>{html.escape(platform)}</b>\n"
        f"Lowongan: {html.escape(job_title)}\n"
        f"Tipe jawaban: <b>{html.escape(kind)}</b>\n\n"
        f"{html.escape(clean_question_text(question))}"
    )
    reply_markup = None
    if kind == "yes_no":
        reply_markup = {"inline_keyboard": [[
            {"text": "Yes", "callback_data": f"qa:{prompt_id}:Yes"},
            {"text": "No", "callback_data": f"qa:{prompt_id}:No"},
        ]]}
    elif kind == "dropdown" and options:
        reply_markup = {"inline_keyboard": [
            [{"text": opt[:60], "callback_data": f"qo:{prompt_id}:{i}"}]
            for i, opt in enumerate(options[:20])
        ]}
        text += "\n\nPilih salah satu tombol di bawah."
    elif kind == "number":
        text += "\n\nBalas pesan ini dengan angka saja."
    else:
        text += "\n\nBalas pesan ini dengan jawaban singkat."

    await send_telegram_message(chat_id, text, reply_markup=reply_markup)


# ── Report formatting (existing) ───────────────────────────────────────

def format_application_report(user_id: int, today_only: bool = True) -> str:
    db = get_db()
    date_filter = "AND date(l.applied_at, 'localtime') = date('now', 'localtime')" if today_only else ""
    rows = db.execute(
        f"""
        SELECT l.platform, l.job_title, l.position, l.company, COALESCE(l.job_location, l.location, '') AS location, l.applied_at
        FROM apply_logs l
        JOIN apply_sessions s ON s.id = l.session_id
        WHERE s.user_id = ?
          AND l.status = 'applied'
          AND l.confirmed_at IS NOT NULL
          {date_filter}
        ORDER BY l.platform ASC, l.applied_at DESC
        LIMIT 80
        """,
        (user_id,),
    ).fetchall()
    db.close()
    title = "Report lamaran hari ini" if today_only else "Report semua lamaran terbaru"
    if not rows:
        return f"<b>{title}</b>\n\nBelum ada lowongan yang tercatat berhasil di-apply."
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row["platform"] or "unknown", []).append(row)
    lines = [f"<b>{title}</b>"]
    for platform, items in grouped.items():
        lines.append(f"\n<b>{html.escape(platform.upper())}</b>")
        for idx, row in enumerate(items, 1):
            title_text = html.escape(row["job_title"] or row["position"] or "Tanpa posisi")
            company = html.escape(row["company"] or "Tanpa perusahaan")
            location = html.escape(row["location"] or "Lokasi tidak tertulis")
            lines.append(f"{idx}. {title_text} | {company} | {location}")
    return "\n".join(lines)[:3900]


async def send_daily_report_to_all_users() -> None:
    db = get_db()
    rows = db.execute("SELECT user_id, chat_id FROM telegram_users WHERE chat_id IS NOT NULL AND enabled=1").fetchall()
    db.close()
    for row in rows:
        await send_telegram_message(row["chat_id"], format_application_report(row["user_id"], today_only=True))


# ── Question answer from Telegram (existing) ──────────────────────────

async def _answer_prompt_from_telegram(user_id: int, prompt_id: str, answer: str, chat_id: str) -> None:
    from workers.session_manager import session_manager

    record = session_manager.get_pending_question(user_id, prompt_id)
    if not record:
        await send_telegram_message(chat_id, "Pertanyaan ini sudah tidak aktif atau sudah dijawab.")
        return
    prompt = record.get("prompt") or {}
    options = prompt.get("options") or parse_prompt_options(prompt.get("question") or "")
    ok, normalized_answer = validate_answer(answer, prompt.get("field_type") or "", prompt.get("question") or "", options)
    if not ok:
        await send_telegram_message(chat_id, normalized_answer)
        return
    success = await session_manager.answer_question_prompt(user_id, prompt_id, normalized_answer)
    if success:
        await send_telegram_message(chat_id, "Jawaban diterima. Bot akan lanjut.")
    else:
        await send_telegram_message(chat_id, "Pertanyaan ini sudah tidak aktif atau sudah dijawab.")


# ═══════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS — Telegram-first UI
# ═══════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "<b>ORDAL — Auto Apply Bot</b>\n\n"
    "<b>Akun:</b>\n"
    "  /register &lt;nama&gt;|&lt;email&gt;|&lt;password&gt; — daftar akun baru\n"
    "  /login &lt;email&gt;|&lt;password&gt; — hubungkan akun existing\n"
    "  /logout — putuskan koneksi Telegram\n"
    "  /profile — lihat info akun\n\n"
    "<b>Target Lowongan:</b>\n"
    "  /targets — daftar target aktif\n"
    "  /target_add — tambah target (interaktif)\n"
    "  /target_del &lt;id&gt; — hapus target\n\n"
    "<b>CV:</b>\n"
    "  /cv — daftar CV\n"
    "  /cv_add — upload CV (reply dengan file PDF)\n"
    "  /cv_del &lt;id&gt; — hapus CV\n\n"
    "<b>Credentials (Cookie Platform):</b>\n"
    "  /credentials — status login per platform\n"
    "  /cookie &lt;linkedin|jobstreet&gt; — upload cookie JSON\n\n"
    "<b>Preferences:</b>\n"
    "  /prefs — lihat preferences\n"
    "  /pref_set &lt;key&gt; &lt;value&gt; — set preference\n"
    "    keys: salary, join, headless, email_test\n\n"
    "<b>Auto-Apply:</b>\n"
    "  /autoapply — lihat jadwal\n"
    "  /autoapply on — aktifkan\n"
    "  /autoapply off — nonaktifkan\n"
    "  /autoapply time 09:00 — set jam\n"
    "  /autoapply days mon,tue,wed,thu,fri — set hari\n\n"
    "<b>Session:</b>\n"
    "  /apply — mulai apply sekarang\n"
    "  /stop — hentikan session aktif\n"
    "  /status — cek progress session\n\n"
    "<b>Question Bank:</b>\n"
    "  /questions — daftar jawaban tersimpan\n"
    "  /question_edit &lt;id&gt; &lt;jawaban baru&gt; — edit jawaban\n\n"
    "<b>Report:</b>\n"
    "  /report — lamaran hari ini\n"
    "  /report all — semua lamaran\n\n"
    "<b>Lainnya:</b>\n"
    "  /help — tampilkan bantuan ini"
)


# ── Auth commands ──────────────────────────────────────────────────────

async def _cmd_register(chat_id: str, args: str):
    """Register akun baru dan langsung link ke chat_id."""
    parts = [p.strip() for p in args.split("|") if p.strip()]
    if len(parts) < 3:
        await send_telegram_message(
            chat_id,
            "Format: /register <i>nama|email|password</i>\n"
            "Contoh: /register Budi|budi@email.com|rahasia123",
        )
        return

    name, email, password = parts[0], parts[1], parts[2]
    if len(password) < 6:
        await send_telegram_message(chat_id, "Password minimal 6 karakter.")
        return

    from auth_utils import hash_password
    db = get_db()
    try:
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            await send_telegram_message(chat_id, f"Email {email} sudah terdaftar. Gunakan /login.")
            return
        hashed = hash_password(password)
        cur = db.execute(
            "INSERT INTO users (email, password, name) VALUES (?, ?, ?)",
            (email, hashed, name),
        )
        user_id = cur.lastrowid
        db.execute(
            """
            INSERT INTO telegram_users (user_id, chat_id, enabled, link_code)
            VALUES (?, ?, 1, ?)
            """,
            (user_id, chat_id, secrets.token_urlsafe(8)[:10].upper()),
        )
        db.commit()
        await send_telegram_message(
            chat_id,
            f"<b>Akun berhasil dibuat!</b>\n"
            f"Nama: {html.escape(name)}\n"
            f"Email: {html.escape(email)}\n\n"
            f"Langkah berikutnya:\n"
            f"1. /cv_add — upload CV\n"
            f"2. /cookie linkedin — upload cookie LinkedIn\n"
            f"3. /target_add — tambah target lowongan\n"
            f"4. /autoapply on — aktifkan auto-apply",
        )
    finally:
        db.close()


async def _cmd_login(chat_id: str, args: str):
    """Login akun existing dan link ke chat_id."""
    parts = [p.strip() for p in args.split("|") if p.strip()]
    if len(parts) < 2:
        await send_telegram_message(
            chat_id,
            "Format: /login <i>email|password</i>\n"
            "Contoh: /login budi@email.com|rahasia123",
        )
        return

    email, password = parts[0], parts[1]
    from auth_utils import verify_password
    db = get_db()
    try:
        user = db.execute("SELECT id, email, name, password FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not verify_password(password, user["password"]):
            await send_telegram_message(chat_id, "Email atau password salah.")
            return
        # Link chat_id
        db.execute(
            """
            INSERT INTO telegram_users (user_id, chat_id, enabled, link_code)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                enabled = 1,
                updated_at = datetime('now')
            """,
            (user["id"], chat_id, secrets.token_urlsafe(8)[:10].upper()),
        )
        db.commit()
        await send_telegram_message(
            chat_id,
            f"<b>Berhasil login!</b>\n"
            f"Nama: {html.escape(user['name'])}\n"
            f"Email: {html.escape(user['email'])}\n\n"
            f"Kirim /help untuk lihat semua perintah.",
        )
    finally:
        db.close()


async def _cmd_logout(chat_id: str):
    """Putuskan koneksi Telegram dari akun."""
    db = get_db()
    try:
        db.execute(
            "UPDATE telegram_users SET chat_id = NULL, enabled = 0, updated_at = datetime('now') WHERE chat_id = ?",
            (chat_id,),
        )
        db.commit()
    finally:
        db.close()
    _conversation_state.pop(chat_id, None)
    await send_telegram_message(chat_id, "Telegram diputus dari akun ORDAL. Kirim /register atau /login untuk menghubungkan lagi.")


async def _cmd_profile(chat_id: str, user: dict):
    db = get_db()
    try:
        # Count targets, CVs, applications
        targets_count = db.execute("SELECT COUNT(*) AS n FROM job_targets WHERE user_id = ? AND active = 1", (user["id"],)).fetchone()["n"]
        cvs_count = db.execute("SELECT COUNT(*) AS n FROM cvs WHERE user_id = ?", (user["id"],)).fetchone()["n"]
        applied_count = db.execute(
            """
            SELECT COUNT(*) AS n FROM apply_logs l
            JOIN apply_sessions s ON s.id = l.session_id
            WHERE s.user_id = ? AND l.status = 'applied' AND l.confirmed_at IS NOT NULL
            """,
            (user["id"],),
        ).fetchone()["n"]
    finally:
        db.close()
    await send_telegram_message(
        chat_id,
        f"<b>Profil Akun</b>\n"
        f"Nama: {html.escape(user['name'])}\n"
        f"Email: {html.escape(user['email'])}\n\n"
        f"Target aktif: <b>{targets_count}</b>\n"
        f"CV tersimpan: <b>{cvs_count}</b>\n"
        f"Total lamaran: <b>{applied_count}</b>",
    )


# ── Target commands ────────────────────────────────────────────────────

async def _cmd_targets(chat_id: str, user: dict):
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT t.id, t.position, t.location, t.platform,
                   COALESCE(t.employment_type, 'full_time') AS employment_type,
                   c.position_label
            FROM job_targets t
            JOIN cvs c ON c.id = t.cv_id
            WHERE t.user_id = ? AND t.active = 1
            ORDER BY t.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    finally:
        db.close()

    if not rows:
        await send_telegram_message(chat_id, "Belum ada target. Kirim /target_add untuk menambahkan.")
        return

    lines = ["<b>Target Aktif</b>\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} • {html.escape(r['position'])} @ {html.escape(r['location'])}\n"
            f"  Platform: {r['platform']} | Tipe: {r['employment_type']} | CV: {html.escape(r['position_label'])}"
        )
    lines.append("\nHapus dengan: /target_del &lt;id&gt;")
    await send_telegram_message(chat_id, "\n".join(lines)[:3900])


async def _cmd_target_add(chat_id: str, user: dict, args: str):
    """Mulai flow tambah target interaktif."""
    # Cek punya CV atau belum
    db = get_db()
    try:
        cvs = db.execute("SELECT id, position_label FROM cvs WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
    finally:
        db.close()
    if not cvs:
        await send_telegram_message(chat_id, "Belum ada CV. Upload dulu via /cv_add sebelum menambah target.")
        return

    # Jika user kasih argumen format: posisi|lokasi|platform
    if args.strip():
        parts = [p.strip() for p in args.split("|") if p.strip()]
        if len(parts) >= 3:
            position, location, platform = parts[0], parts[1], parts[2]
            cv_id = cvs[0]["id"]
            await _create_target_from_telegram(chat_id, user["id"], cv_id, position, location, platform)
            return

    # Flow interaktif: simpan state
    _conversation_state[chat_id] = {
        "step": "target_add_position",
        "data": {"user_id": user["id"]},
    }
    await send_telegram_message(
        chat_id,
        "<b>Tambah Target</b>\n"
        "Masukkan <b>posisi</b> yang dicari (contoh: Purchasing Staff):",
    )


async def _create_target_from_telegram(chat_id: str, user_id: int, cv_id: int, position: str, location: str, platform: str):
    valid = ("linkedin", "linkedin_posts", "jobstreet", "both", "all")
    if platform not in valid:
        await send_telegram_message(chat_id, f"Platform tidak valid. Pilih: {', '.join(valid)}")
        return
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO job_targets (user_id, cv_id, position, location, platform, employment_type)
            VALUES (?, ?, ?, ?, ?, 'full_time')
            """,
            (user_id, cv_id, position, location, platform),
        )
        db.commit()
    finally:
        db.close()
    await send_telegram_message(
        chat_id,
        f"<b>Target ditambahkan!</b>\n"
        f"Posisi: {html.escape(position)}\n"
        f"Lokasi: {html.escape(location)}\n"
        f"Platform: {platform}\n\n"
        f"Gunakan /targets untuk lihat semua target.",
    )


async def _cmd_target_del(chat_id: str, user: dict, args: str):
    if not args.strip().isdigit():
        await send_telegram_message(chat_id, "Format: /target_del <id>")
        return
    target_id = int(args.strip())
    db = get_db()
    try:
        row = db.execute("SELECT id FROM job_targets WHERE id = ? AND user_id = ?", (target_id, user["id"])).fetchone()
        if not row:
            await send_telegram_message(chat_id, f"Target #{target_id} tidak ditemukan.")
            return
        db.execute("DELETE FROM job_targets WHERE id = ?", (target_id,))
        db.commit()
    finally:
        db.close()
    await send_telegram_message(chat_id, f"Target #{target_id} dihapus.")


# ── CV commands ────────────────────────────────────────────────────────

async def _cmd_cv(chat_id: str, user: dict):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, position_label, file_name, created_at FROM cvs WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    finally:
        db.close()
    if not rows:
        await send_telegram_message(chat_id, "Belum ada CV. Upload via /cv_add (reply dengan file PDF).")
        return
    lines = ["<b>Daftar CV</b>\n"]
    for r in rows:
        lines.append(f"#{r['id']} • {html.escape(r['position_label'])}\n  File: {html.escape(r['file_name'])}")
    lines.append("\nHapus dengan: /cv_del <id>")
    await send_telegram_message(chat_id, "\n".join(lines)[:3900])


async def _cmd_cv_add(chat_id: str):
    _conversation_state[chat_id] = {"step": "cv_upload_waiting", "data": {}}
    await send_telegram_message(
        chat_id,
        "<b>Upload CV</b>\n"
        "Kirim file PDF CV Anda sekarang. Setelah file diterima, "
        "bot akan menanyakan label posisi untuk CV tersebut.",
    )


async def _cmd_cv_del(chat_id: str, user: dict, args: str):
    if not args.strip().isdigit():
        await send_telegram_message(chat_id, "Format: /cv_del <id>")
        return
    cv_id = int(args.strip())
    db = get_db()
    try:
        row = db.execute("SELECT file_path FROM cvs WHERE id = ? AND user_id = ?", (cv_id, user["id"])).fetchone()
        if not row:
            await send_telegram_message(chat_id, f"CV #{cv_id} tidak ditemukan.")
            return
        try:
            os.remove(row["file_path"])
        except FileNotFoundError:
            pass
        db.execute("DELETE FROM job_targets WHERE cv_id = ? AND user_id = ?", (cv_id, user["id"]))
        db.execute("DELETE FROM cvs WHERE id = ?", (cv_id,))
        db.commit()
    finally:
        db.close()
    await send_telegram_message(chat_id, f"CV #{cv_id} dihapus.")


# ── Credential commands ────────────────────────────────────────────────

async def _cmd_credentials(chat_id: str, user: dict):
    from routers.credentials import cookies_path, PLATFORM_CONFIG
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT platform, COALESCE(cookie_valid, 1) AS cookie_valid, last_cookie_warning_at
            FROM user_credentials WHERE user_id = ?
            """,
            (user["id"],),
        ).fetchall()
    finally:
        db.close()

    lines = ["<b>Status Credentials</b>\n"]
    for platform_name in ("linkedin", "jobstreet"):
        path = cookies_path(user["id"], platform_name)
        has_file = os.path.exists(path)
        row = next((r for r in rows if r["platform"] == platform_name), None)
        marked_valid = bool(row and row["cookie_valid"]) if row else True
        status = "✅ Valid" if (has_file and marked_valid) else "❌ Expired/missing"
        lines.append(f"<b>{platform_name.upper()}</b>: {status}")
        if not has_file:
            lines.append(f"  Upload via: /cookie {platform_name}")
    await send_telegram_message(chat_id, "\n".join(lines))


async def _cmd_cookie(chat_id: str, user: dict, args: str):
    platform = args.strip().lower()
    if platform not in ("linkedin", "jobstreet"):
        await send_telegram_message(
            chat_id,
            "Format: /cookie <i>linkedin</i> atau /cookie <i>jobstreet</i>\n\n"
            "Setelah perintah ini, kirim file JSON cookie Anda.\n\n"
            "<b>Cara export cookie:</b>\n"
            "1. Install extension 'Cookie Editor' di browser\n"
            "2. Login ke platform target\n"
            "3. Buka extension → Export → JSON format\n"
            "4. Save file, kirim ke bot ini setelah perintah /cookie",
        )
        return
    _conversation_state[chat_id] = {"step": "cookie_upload_waiting", "data": {"platform": platform, "user_id": user["id"]}}
    await send_telegram_message(
        chat_id,
        f"<b>Upload Cookie {platform.upper()}</b>\n"
        f"Kirim file JSON cookie sekarang. Bot akan menyimpannya dan menandai platform sebagai valid.",
    )


# ── Preference commands ────────────────────────────────────────────────

async def _cmd_prefs(chat_id: str, user: dict):
    db = get_db()
    try:
        row = db.execute(
            """
            SELECT expected_salary, available_join,
                   COALESCE(headless_mode, 0) AS headless_mode,
                   COALESCE(testing_email_mode, 0) AS testing_email_mode,
                   COALESCE(auto_apply_enabled, 0) AS auto_apply_enabled,
                   COALESCE(auto_apply_hour, 9) AS auto_apply_hour,
                   COALESCE(auto_apply_minute, 0) AS auto_apply_minute,
                   COALESCE(auto_apply_days, 'mon,tue,wed,thu,fri') AS auto_apply_days
            FROM user_preferences WHERE user_id = ?
            """,
            (user["id"],),
        ).fetchone()
    finally:
        db.close()
    if not row:
        await send_telegram_message(chat_id, "Preferences belum diset. Gunakan /pref_set untuk mengatur.")
        return
    await send_telegram_message(
        chat_id,
        f"<b>Preferences</b>\n"
        f"Expected Salary: <b>{html.escape(row['expected_salary'] or '-')}</b>\n"
        f"Available Join: <b>{html.escape(row['available_join'] or '-')}</b>\n"
        f"Headless Mode: {'ON' if row['headless_mode'] else 'OFF'}\n"
        f"Testing Email: {'ON' if row['testing_email_mode'] else 'OFF'}\n\n"
        f"<b>Auto-Apply:</b>\n"
        f"Status: {'AKTIF' if row['auto_apply_enabled'] else 'NONAKTIF'}\n"
        f"Jadwal: {row['auto_apply_hour']:02d}:{row['auto_apply_minute']:02d} WIB\n"
        f"Hari: {row['auto_apply_days']}\n\n"
        f"Set dengan: /pref_set &lt;key&gt; &lt;value&gt;\n"
        f"  salary 7000000\n"
        f"  join '1 month'\n"
        f"  headless on\n"
        f"  email_test off",
    )


async def _cmd_pref_set(chat_id: str, user: dict, args: str):
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        await send_telegram_message(chat_id, "Format: /pref_set <key> <value>\nKeys: salary, join, headless, email_test")
        return
    key, value = parts[0].lower(), parts[1].strip()
    db = get_db()
    try:
        if key in ("salary", "expected_salary"):
            db.execute(
                """
                INSERT INTO user_preferences (user_id, expected_salary, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET expected_salary = excluded.expected_salary, updated_at = datetime('now')
                """,
                (user["id"], value),
            )
        elif key in ("join", "available_join"):
            db.execute(
                """
                INSERT INTO user_preferences (user_id, available_join, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET available_join = excluded.available_join, updated_at = datetime('now')
                """,
                (user["id"], value),
            )
        elif key == "headless":
            val = 1 if value.lower() in ("on", "1", "true", "ya") else 0
            db.execute(
                """
                INSERT INTO user_preferences (user_id, headless_mode, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET headless_mode = excluded.headless_mode, updated_at = datetime('now')
                """,
                (user["id"], val),
            )
        elif key in ("email_test", "testing_email"):
            val = 1 if value.lower() in ("on", "1", "true", "ya") else 0
            db.execute(
                """
                INSERT INTO user_preferences (user_id, testing_email_mode, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET testing_email_mode = excluded.testing_email_mode, updated_at = datetime('now')
                """,
                (user["id"], val),
            )
        else:
            await send_telegram_message(chat_id, f"Key tidak dikenal: {key}. Pilihan: salary, join, headless, email_test")
            return
        db.commit()
    finally:
        db.close()
    await send_telegram_message(chat_id, f"Preference <b>{key}</b> diupdate ke: {html.escape(value)}")


# ── Auto-apply commands ────────────────────────────────────────────────

async def _cmd_autoapply(chat_id: str, user: dict, args: str):
    parts = args.strip().split(None, 1)
    if not parts:
        # Show current status
        await _cmd_prefs(chat_id, user)
        return

    action = parts[0].lower()
    db = get_db()
    try:
        if action == "on":
            db.execute(
                """
                INSERT INTO user_preferences (user_id, auto_apply_enabled, updated_at)
                VALUES (?, 1, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET auto_apply_enabled = 1, updated_at = datetime('now')
                """,
                (user["id"],),
            )
            db.commit()
            await send_telegram_message(chat_id, "✅ <b>Auto-apply diaktifkan!</b>\nBot akan otomatis apply setiap hari sesuai jadwal.")
        elif action == "off":
            db.execute(
                """
                INSERT INTO user_preferences (user_id, auto_apply_enabled, updated_at)
                VALUES (?, 0, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET auto_apply_enabled = 0, updated_at = datetime('now')
                """,
                (user["id"],),
            )
            db.commit()
            await send_telegram_message(chat_id, "❌ Auto-apply dinonaktifkan.")
        elif action == "time" and len(parts) > 1:
            time_str = parts[1].strip()
            match = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
            if not match:
                await send_telegram_message(chat_id, "Format jam: HH:MM (contoh: 09:00)")
                return
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                await send_telegram_message(chat_id, "Jam tidak valid. Gunakan format 24 jam.")
                return
            db.execute(
                """
                INSERT INTO user_preferences (user_id, auto_apply_hour, auto_apply_minute, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET auto_apply_hour = excluded.auto_apply_hour, auto_apply_minute = excluded.auto_apply_minute, updated_at = datetime('now')
                """,
                (user["id"], hour, minute),
            )
            db.commit()
            await send_telegram_message(chat_id, f"Jadwal auto-apply diatur ke <b>{hour:02d}:{minute:02d} WIB</b>.")
        elif action == "days" and len(parts) > 1:
            days_str = parts[1].strip().lower()
            # Validate
            valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
            for d in days_str.split(","):
                if d.strip() not in valid_days:
                    await send_telegram_message(chat_id, f"Hari tidak valid: {d}. Gunakan: mon,tue,wed,thu,fri,sat,sun")
                    return
            db.execute(
                """
                INSERT INTO user_preferences (user_id, auto_apply_days, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(user_id) DO UPDATE SET auto_apply_days = excluded.auto_apply_days, updated_at = datetime('now')
                """,
                (user["id"], days_str),
            )
            db.commit()
            await send_telegram_message(chat_id, f"Hari auto-apply diatur ke: <b>{days_str}</b>")
        else:
            await send_telegram_message(
                chat_id,
                "Format: /autoapply [on|off|time|days]\n"
                "Contoh:\n"
                "  /autoapply on\n"
                "  /autoapply time 09:00\n"
                "  /autoapply days mon,tue,wed,thu,fri",
            )
    finally:
        db.close()


# ── Session commands ───────────────────────────────────────────────────

async def _cmd_apply(chat_id: str, user: dict):
    from workers.session_manager import session_manager
    if session_manager.has_active_session(user["id"]):
        await send_telegram_message(chat_id, "Session sedang berjalan. Kirim /stop untuk menghentikan.")
        return
    await send_telegram_message(chat_id, "⏳ Memulai session apply...")
    result = await session_manager.start_session_for_user(user_id=user["id"], source="manual")
    if result.get("ok"):
        await send_telegram_message(
            chat_id,
            f"<b>Session dimulai!</b>\nID: {result['session_id']}\n"
            f"Kirim /status untuk cek progress, /stop untuk berhenti.",
        )
    else:
        await send_telegram_message(chat_id, f"❌ Gagal memulai: {result.get('message', 'Unknown error')}")


async def _cmd_stop(chat_id: str, user: dict):
    from workers.session_manager import session_manager
    db = get_db()
    try:
        session = db.execute(
            "SELECT id FROM apply_sessions WHERE user_id = ? AND status = 'running'",
            (user["id"],),
        ).fetchone()
        if session:
            db.execute("UPDATE apply_sessions SET status='stopped', ended_at=datetime('now') WHERE id=?", (session["id"],))
            db.commit()
    finally:
        db.close()
    await session_manager.stop_session(user["id"])
    await send_telegram_message(chat_id, "⏹ Session dihentikan.")


async def _cmd_status(chat_id: str, user: dict):
    from workers.session_manager import session_manager
    status = session_manager.get_session_status(user["id"])
    if not status.get("running"):
        await send_telegram_message(chat_id, "Tidak ada session aktif. Kirim /apply untuk memulai.")
        return
    await send_telegram_message(
        chat_id,
        f"<b>Session Aktif</b>\n"
        f"ID: {status['session_id']}\n"
        f"Sumber: {status.get('source', 'manual')}\n"
        f"Mulai: {status.get('started_at', '-')}\n\n"
        f"✅ Applied: <b>{status['applied']}</b>\n"
        f"⏭ Skipped: {status['skipped']}\n"
        f"❌ Failed: {status['failed']}",
    )


# ── Question bank commands ─────────────────────────────────────────────

async def _cmd_questions(chat_id: str, user: dict):
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT id, platform, question, answer, source, field_type
            FROM question_bank WHERE user_id = ?
            ORDER BY updated_at DESC LIMIT 20
            """,
            (user["id"],),
        ).fetchall()
    finally:
        db.close()
    if not rows:
        await send_telegram_message(chat_id, "Belum ada jawaban tersimpan di question bank.")
        return
    lines = ["<b>Question Bank (20 terbaru)</b>\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} [{r['source']}] {html.escape(r['platform'] or 'all')}\n"
            f"  Q: {html.escape((r['question'] or '')[:100])}\n"
            f"  A: {html.escape((r['answer'] or '')[:100])}"
        )
    lines.append("\nEdit dengan: /question_edit <id> <jawaban baru>")
    await send_telegram_message(chat_id, "\n".join(lines)[:3900])


async def _cmd_question_edit(chat_id: str, user: dict, args: str):
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        await send_telegram_message(chat_id, "Format: /question_edit <id> <jawaban baru>")
        return
    qid = parts[0]
    new_answer = parts[1].strip()
    if not qid.isdigit():
        await send_telegram_message(chat_id, "ID harus angka.")
        return
    db = get_db()
    try:
        row = db.execute("SELECT id FROM question_bank WHERE id = ? AND user_id = ?", (int(qid), user["id"])).fetchone()
        if not row:
            await send_telegram_message(chat_id, f"Pertanyaan #{qid} tidak ditemukan.")
            return
        db.execute(
            """
            UPDATE question_bank SET answer = ?, source = 'manual', updated_at = datetime('now') WHERE id = ?
            """,
            (new_answer, int(qid)),
        )
        db.commit()
    finally:
        db.close()
    await send_telegram_message(chat_id, f"Jawaban #{qid} diupdate ke:\n{html.escape(new_answer[:200])}")


# ── File upload handler ────────────────────────────────────────────────

async def _handle_document(message: dict):
    """Handle file upload dari user (PDF CV atau JSON cookie)."""
    chat_id = str(message.get("chat", {}).get("id") or "")
    document = message.get("document") or {}
    file_name = document.get("file_name") or ""
    file_id = document.get("file_id") or ""
    if not chat_id or not file_id:
        return

    state = _conversation_state.get(chat_id)
    caption = (message.get("caption") or "").strip()

    # Jika tidak ada state, tebak dari caption / extension
    if not state:
        if caption.lower().startswith("/cookie") or file_name.lower().endswith(".json"):
            user = _get_user_by_chat(chat_id)
            if not user:
                await send_telegram_message(chat_id, "Akun belum terhubung. /register atau /login dulu.")
                return
            parts = caption.split()
            platform = parts[1].lower() if len(parts) > 1 else ""
            if platform not in ("linkedin", "jobstreet"):
                await send_telegram_message(chat_id, "Sebutkan platform: kirim /cookie linkedin atau /cookie jobstreet lalu file JSON.")
                return
            await _save_cookie_file(chat_id, user["id"], platform, file_id, file_name)
            return
        if file_name.lower().endswith(".pdf"):
            user = _get_user_by_chat(chat_id)
            if not user:
                await send_telegram_message(chat_id, "Akun belum terhubung. /register atau /login dulu.")
                return
            position_label = caption or "General"
            await _save_cv_file(chat_id, user["id"], file_id, file_name, position_label)
            return
        await send_telegram_message(chat_id, "File tidak dikenali. Untuk CV kirim PDF, untuk cookie kirim JSON setelah /cookie <platform>.")
        return

    step = state.get("step")
    data = state.get("data", {})

    if step == "cookie_upload_waiting":
        platform = data.get("platform")
        user_id = data.get("user_id")
        if not file_name.lower().endswith(".json"):
            await send_telegram_message(chat_id, "File harus JSON. Export cookie dari browser extension dalam format JSON.")
            return
        await _save_cookie_file(chat_id, user_id, platform, file_id, file_name)
        _conversation_state.pop(chat_id, None)

    elif step == "cv_upload_waiting":
        user_id = data.get("user_id")
        if not file_name.lower().endswith(".pdf"):
            await send_telegram_message(chat_id, "File harus PDF.")
            return
        # Tanya position label dulu, atau pakai caption
        position_label = caption.strip() if caption else "General"
        await _save_cv_file(chat_id, user_id, file_id, file_name, position_label)
        _conversation_state.pop(chat_id, None)

    else:
        await send_telegram_message(chat_id, "Upload tidak diharapkan saat ini. Kirim /help untuk bantuan.")


async def _save_cookie_file(chat_id: str, user_id: int, platform: str, file_id: str, file_name: str):
    """Download & simpan cookie JSON sebagai Playwright storage_state."""
    from routers.credentials import cookies_path, save_credential_marker, PLATFORM_CONFIG
    await send_telegram_message(chat_id, f"⏳ Mengunduh file cookie {platform}...")
    content = await _download_telegram_file(file_id)
    if not content:
        await send_telegram_message(chat_id, "❌ Gagal download file dari Telegram.")
        return

    # Validasi JSON
    import json
    try:
        data = json.loads(content)
        # Playwright storage_state format: {"cookies": [...], "origins": [...]}
        if "cookies" not in data:
            # Mungkin format Cookie Editor: [{"name":"...","value":"...",...}]
            # Convert ke Playwright format
            data = {"cookies": data, "origins": []}
            content = json.dumps(data).encode()
    except json.JSONDecodeError:
        await send_telegram_message(chat_id, "❌ File bukan JSON valid. Pastikan export cookie dalam format JSON.")
        return

    # Simpan file
    path = cookies_path(user_id, platform)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)

    # Update DB
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO user_credentials (user_id, platform, email, password, cookie_valid, last_cookie_warning_at)
            VALUES (?, ?, 'telegram_upload', '', 1, NULL)
            ON CONFLICT(user_id, platform) DO UPDATE SET
                email = 'telegram_upload',
                cookie_valid = 1,
                last_cookie_warning_at = NULL,
                updated_at = datetime('now')
            """,
            (user_id, platform),
        )
        db.commit()

        # Backup cookie ke kolom DB (persisten lewat Turso kalau dipakai),
        # supaya bisa direstore kalau disk lokal hilang (host ephemeral).
        import base64
        db.execute(
            "UPDATE user_credentials SET cookie_data = ? WHERE user_id = ? AND platform = ?",
            (base64.b64encode(content).decode("ascii"), user_id, platform),
        )
        db.commit()
    finally:
        db.close()

    await send_telegram_message(
        chat_id,
        f"✅ <b>Cookie {platform.upper()} tersimpan!</b>\n"
        f"File: {html.escape(file_name)}\n"
        f"Platform siap digunakan untuk apply.",
    )


async def _save_cv_file(chat_id: str, user_id: int, file_id: str, file_name: str, position_label: str):
    """Download & simpan CV PDF."""
    await send_telegram_message(chat_id, "⏳ Mengunduh CV...")
    content = await _download_telegram_file(file_id)
    if not content:
        await send_telegram_message(chat_id, "❌ Gagal download file dari Telegram.")
        return

    # Safe filename
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", file_name)
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    upload_dir = os.path.join("uploads", "cvs", str(user_id))
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, safe_name)
    with open(file_path, "wb") as f:
        f.write(content)

    # Extract text
    cv_text = ""
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            cv_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        pass

    # Save to DB
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO cvs (user_id, position_label, file_name, file_path, cv_text) VALUES (?, ?, ?, ?, ?)",
            (user_id, position_label, file_name, file_path, cv_text),
        )
        cv_id = cur.lastrowid
        db.commit()

        from database import backup_file_to_db
        backup_file_to_db("cvs", "id", cv_id, "file_data", content)
    finally:
        db.close()

    has_text = "✅ Teks terbaca" if cv_text else "⚠️ Teks tidak terbaca (mungkin scan gambar)"
    await send_telegram_message(
        chat_id,
        f"✅ <b>CV tersimpan!</b>\n"
        f"ID: {cv_id}\n"
        f"Label: {html.escape(position_label)}\n"
        f"File: {html.escape(file_name)}\n"
        f"{has_text}\n\n"
        f"Gunakan /target_add untuk membuat target dengan CV ini.",
    )


# ── Conversation state handlers (multi-step target_add) ────────────────

async def _handle_conversation(chat_id: str, text: str, user: dict) -> bool:
    """Handle pesan dalam conversation state. Return True jika pesan dikonsumsi."""
    state = _conversation_state.get(chat_id)
    if not state:
        return False

    step = state.get("step")
    data = state.get("data", {})

    if step == "target_add_position":
        data["position"] = text.strip()
        state["step"] = "target_add_location"
        await send_telegram_message(chat_id, f"Posisi: <b>{html.escape(text.strip())}</b>\nMasukkan <b>lokasi</b> (contoh: Jakarta):")
        return True

    if step == "target_add_location":
        data["location"] = text.strip()
        # Show platform options
        state["step"] = "target_add_platform"
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "LinkedIn", "callback_data": "tp:linkedin"},
                    {"text": "JobStreet", "callback_data": "tp:jobstreet"},
                ],
                [
                    {"text": "LinkedIn Posts", "callback_data": "tp:linkedin_posts"},
                    {"text": "Both (LI+JS)", "callback_data": "tp:both"},
                ],
                [{"text": "All", "callback_data": "tp:all"}],
            ]
        }
        await send_telegram_message(
            chat_id,
            f"Lokasi: <b>{html.escape(text.strip())}</b>\nPilih <b>platform</b>:",
            reply_markup=keyboard,
        )
        return True

    if step == "target_add_cv":
        # Tidak seharusnya sampai sini via text — pakai inline keyboard
        pass

    return False


# ── Main message handler ───────────────────────────────────────────────

async def _handle_message(message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id") or "")

    # Handle document uploads
    if "document" in message:
        user = _get_user_by_chat(chat_id)
        if not user:
            await send_telegram_message(chat_id, "Akun belum terhubung. /register atau /login dulu.")
            return
        await _handle_document(message)
        return

    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    # ── /start (link code) — backward compat ──
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            # Cek apakah user sudah terhubung
            user = _get_user_by_chat(chat_id)
            if user:
                await send_telegram_message(chat_id, f"Halo {html.escape(user['name'])}! Kirim /help untuk lihat perintah.")
            else:
                await send_telegram_message(
                    chat_id,
                    "Selamat datang di ORDAL Auto Apply Bot!\n\n"
                    "Daftar akun baru: /register <i>nama|email|password</i>\n"
                    "Login akun existing: /login <i>email|password</i>\n\n"
                    "Contoh: /register Budi|budi@email.com|rahasia123",
                )
            return
        code = parts[1].strip().upper()
        db = get_db()
        row = db.execute("SELECT user_id FROM telegram_users WHERE link_code=?", (code,)).fetchone()
        if not row:
            db.close()
            await send_telegram_message(chat_id, "Kode tidak valid. Gunakan /register atau /login.")
            return
        db.execute("UPDATE telegram_users SET chat_id=?, enabled=1, updated_at=datetime('now') WHERE user_id=?", (chat_id, row["user_id"]))
        db.commit()
        db.close()
        await send_telegram_message(chat_id, "✅ Telegram terhubung dengan ORDAL. Kirim /help untuk lihat perintah.")
        return

    # ── Commands yang tidak butuh login ──
    cmd_parts = text.split(maxsplit=1)
    cmd = cmd_parts[0].lower()
    args = cmd_parts[1] if len(cmd_parts) > 1 else ""

    if cmd == "/register":
        await _cmd_register(chat_id, args)
        return
    if cmd == "/login":
        await _cmd_login(chat_id, args)
        return
    if cmd == "/help":
        await send_telegram_message(chat_id, HELP_TEXT)
        return

    # ── Cek login untuk command selanjutnya ──
    user = _get_user_by_chat(chat_id)
    if not user:
        await send_telegram_message(
            chat_id,
            "Akun belum terhubung.\n/register <i>nama|email|password</i>\n/login <i>email|password</i>",
        )
        return

    # ── Handle conversation state (multi-step flows) ──
    if await _handle_conversation(chat_id, text, user):
        return

    # ── Command yang butuh login ──
    if cmd == "/logout":
        await _cmd_logout(chat_id)
        return
    if cmd == "/profile":
        await _cmd_profile(chat_id, user)
        return
    if cmd == "/targets":
        await _cmd_targets(chat_id, user)
        return
    if cmd == "/target_add":
        await _cmd_target_add(chat_id, user, args)
        return
    if cmd == "/target_del":
        await _cmd_target_del(chat_id, user, args)
        return
    if cmd == "/cv":
        await _cmd_cv(chat_id, user)
        return
    if cmd == "/cv_add":
        await _cmd_cv_add(chat_id)
        return
    if cmd == "/cv_del":
        await _cmd_cv_del(chat_id, user, args)
        return
    if cmd == "/credentials":
        await _cmd_credentials(chat_id, user)
        return
    if cmd == "/cookie":
        await _cmd_cookie(chat_id, user, args)
        return
    if cmd == "/prefs":
        await _cmd_prefs(chat_id, user)
        return
    if cmd == "/pref_set":
        await _cmd_pref_set(chat_id, user, args)
        return
    if cmd == "/autoapply":
        await _cmd_autoapply(chat_id, user, args)
        return
    if cmd == "/apply":
        await _cmd_apply(chat_id, user)
        return
    if cmd == "/stop":
        await _cmd_stop(chat_id, user)
        return
    if cmd == "/status":
        await _cmd_status(chat_id, user)
        return
    if cmd == "/questions":
        await _cmd_questions(chat_id, user)
        return
    if cmd == "/question_edit":
        await _cmd_question_edit(chat_id, user, args)
        return
    if cmd == "/report":
        today_only = "all" not in text.lower() and "semua" not in text.lower()
        await send_telegram_message(chat_id, format_application_report(user["id"], today_only=today_only))
        return

    # ── Jika ada session aktif & ada pending question, anggap jawaban ──
    from workers.session_manager import session_manager
    pending = session_manager.latest_pending_question(user["id"])
    if pending:
        await _answer_prompt_from_telegram(user["id"], pending[0], text, chat_id)
        return

    await send_telegram_message(chat_id, f"Perintah tidak dikenal: {cmd}\nKirim /help untuk daftar perintah.")


# ── Callback handler (inline keyboards) ────────────────────────────────

async def _handle_callback(callback: dict) -> None:
    data_str = callback.get("data") or ""
    message = callback.get("message") or {}
    chat_id = str(message.get("chat", {}).get("id") or "")
    if not chat_id:
        return

    # ── Question answer callbacks (qa:, qo:) ──
    if data_str.startswith(("qa:", "qo:")):
        user = _get_user_by_chat(chat_id)
        if not user:
            await send_telegram_message(chat_id, "Akun belum terhubung.")
            return
        user_id = user["id"]
        try:
            kind, prompt_id, value = data_str.split(":", 2)
        except ValueError:
            return
        if kind == "qo":
            from workers.session_manager import session_manager
            record = session_manager.get_pending_question(user_id, prompt_id)
            prompt = (record or {}).get("prompt") or {}
            options = prompt.get("options") or parse_prompt_options(prompt.get("question") or "")
            try:
                value = options[int(value)]
            except Exception:
                await send_telegram_message(chat_id, "Opsi tidak valid atau pertanyaan sudah tidak aktif.")
                return
        await _answer_prompt_from_telegram(user_id, prompt_id, value, chat_id)
        return

    # ── Target add platform callback (tp:) ──
    if data_str.startswith("tp:"):
        user = _get_user_by_chat(chat_id)
        if not user:
            await send_telegram_message(chat_id, "Akun belum terhubung.")
            return
        platform = data_str[3:]
        state = _conversation_state.get(chat_id, {})
        if state.get("step") != "target_add_platform":
            await send_telegram_message(chat_id, "Sesi tambah target sudah kedaluwarsa. Kirim /target_add untuk mulai lagi.")
            return
        position = state.get("data", {}).get("position", "")
        location = state.get("data", {}).get("location", "")
        if not position or not location:
            await send_telegram_message(chat_id, "Data tidak lengkap. Kirim /target_add untuk mulai lagi.")
            _conversation_state.pop(chat_id, None)
            return

        # Ambil CV pertama
        db = get_db()
        try:
            cv = db.execute("SELECT id FROM cvs WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user["id"],)).fetchone()
        finally:
            db.close()
        if not cv:
            await send_telegram_message(chat_id, "Belum ada CV. Upload via /cv_add dulu.")
            _conversation_state.pop(chat_id, None)
            return

        await _create_target_from_telegram(chat_id, user["id"], cv["id"], position, location, platform)
        _conversation_state.pop(chat_id, None)
        return

    await send_telegram_message(chat_id, "Aksi tidak dikenal.")


# ── Polling loop & schedulers (existing) ───────────────────────────────

async def poll_telegram_updates() -> None:
    global _update_offset
    if not telegram_available() or not TELEGRAM_POLLING_ENABLED:
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                res = await client.get(_api_url("getUpdates"), params={"timeout": 25, "offset": _update_offset})
                payload = res.json() if res.status_code == 200 else {"result": []}
            for update in payload.get("result", []):
                _update_offset = max(_update_offset, int(update.get("update_id", 0)) + 1)
                if "message" in update:
                    await _handle_message(update["message"])
                if "callback_query" in update:
                    await _handle_callback(update["callback_query"])
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


async def daily_report_scheduler() -> None:
    global _last_report_date
    if not telegram_available():
        return
    while True:
        now = datetime.now(APP_TZ)
        if now.hour == TELEGRAM_REPORT_HOUR and now.minute >= TELEGRAM_REPORT_MINUTE and _last_report_date != now.date():
            _last_report_date = now.date()
            await send_daily_report_to_all_users()
        await asyncio.sleep(30)


def start_background_tasks() -> None:
    global _polling_task, _report_task
    if not telegram_available():
        return
    loop = asyncio.get_running_loop()
    if TELEGRAM_POLLING_ENABLED and (_polling_task is None or _polling_task.done()):
        _polling_task = loop.create_task(poll_telegram_updates())
    if _report_task is None or _report_task.done():
        _report_task = loop.create_task(daily_report_scheduler())


async def stop_background_tasks() -> None:
    for task in (_polling_task, _report_task):
        if task:
            task.cancel()
