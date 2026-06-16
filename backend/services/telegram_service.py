import asyncio
import html
import os
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


def parse_prompt_options(question: str) -> list[str]:
    import re
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
    import re
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
        import re
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


async def _handle_message(message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id") or "")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            await send_telegram_message(chat_id, "Kirim /start KODE_LINK dari halaman Settings ORDAL untuk menghubungkan akun.")
            return
        code = parts[1].strip().upper()
        db = get_db()
        row = db.execute("SELECT user_id FROM telegram_users WHERE link_code=?", (code,)).fetchone()
        if not row:
            db.close()
            await send_telegram_message(chat_id, "Kode tidak valid. Buat kode baru dari Settings ORDAL.")
            return
        db.execute("UPDATE telegram_users SET chat_id=?, enabled=1, updated_at=datetime('now') WHERE user_id=?", (chat_id, row["user_id"]))
        db.commit()
        db.close()
        await send_telegram_message(chat_id, "Telegram sudah terhubung dengan ORDAL. Gunakan /report untuk cek lamaran kapan saja.")
        return

    db = get_db()
    link = db.execute("SELECT user_id FROM telegram_users WHERE chat_id=? AND enabled=1", (chat_id,)).fetchone()
    db.close()
    if not link:
        await send_telegram_message(chat_id, "Telegram belum terhubung. Gunakan /start KODE_LINK dari Settings ORDAL.")
        return
    user_id = int(link["user_id"])

    if text.lower().startswith("/report"):
        today_only = "all" not in text.lower() and "semua" not in text.lower()
        await send_telegram_message(chat_id, format_application_report(user_id, today_only=today_only))
        return

    from workers.session_manager import session_manager
    pending = session_manager.latest_pending_question(user_id)
    if not pending:
        await send_telegram_message(chat_id, "Tidak ada pertanyaan aktif. Gunakan /report untuk cek lamaran.")
        return
    await _answer_prompt_from_telegram(user_id, pending[0], text, chat_id)


async def _handle_callback(callback: dict) -> None:
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat_id = str(message.get("chat", {}).get("id") or "")
    if not chat_id:
        return
    db = get_db()
    link = db.execute("SELECT user_id FROM telegram_users WHERE chat_id=? AND enabled=1", (chat_id,)).fetchone()
    db.close()
    if not link:
        await send_telegram_message(chat_id, "Telegram belum terhubung.")
        return
    user_id = int(link["user_id"])
    try:
        kind, prompt_id, value = data.split(":", 2)
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
