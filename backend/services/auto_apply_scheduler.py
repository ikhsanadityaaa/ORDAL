"""
Auto-apply scheduler: loop async yang mengecek jadwal per-user setiap 60 detik
dan memulai apply session otomatis untuk user yang sudah mengaktifkan auto-apply.

Dijalankan sebagai background task di startup FastAPI (bersama telegram polling).
"""
import asyncio
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

from database import get_db

APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Jakarta"))

# Mapping nama hari ke angka (Monday=0 ... Sunday=6)
WEEKDAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

_scheduler_task: asyncio.Task | None = None
_last_check_minute: datetime | None = None


def _parse_days(days_str: str) -> set[int]:
    """Parse 'mon,tue,wed,thu,fri' jadi set of weekday integers."""
    if not days_str:
        return {0, 1, 2, 3, 4}  # default Mon-Fri
    result: set[int] = set()
    for part in days_str.split(","):
        key = part.strip().lower()
        if key in WEEKDAY_MAP:
            result.add(WEEKDAY_MAP[key])
    return result or {0, 1, 2, 3, 4}


def _already_applied_today(user_id: int, today: date) -> bool:
    """Cek apakah user sudah auto-apply hari ini."""
    db = get_db()
    try:
        row = db.execute(
            """
            SELECT last_auto_apply_at FROM user_preferences
            WHERE user_id = ? AND auto_apply_enabled = 1
            """,
            (user_id,),
        ).fetchone()
        if not row or not row["last_auto_apply_at"]:
            return False
        try:
            last = datetime.fromisoformat(row["last_auto_apply_at"]).astimezone(APP_TZ)
            return last.date() >= today
        except Exception:
            return False
    finally:
        db.close()


def _mark_auto_applied(user_id: int):
    """Tandai bahwa user sudah auto-apply sekarang."""
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO user_preferences (user_id, last_auto_apply_at, updated_at)
            VALUES (?, datetime('now'), datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                last_auto_apply_at = excluded.last_auto_apply_at,
                updated_at = datetime('now')
            """,
            (user_id,),
        )
        db.commit()
    finally:
        db.close()


def _get_eligible_users(now: datetime) -> list[dict]:
    """
    Ambil semua user yang:
    - auto_apply_enabled = 1
    - jadwal jam/menit cocok dengan now
    - hari ini ada di auto_apply_days
    - belum auto-apply hari ini
    """
    today = now.date()
    weekday = today.weekday()  # Monday=0, Sunday=6

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT
                p.user_id,
                p.auto_apply_hour,
                p.auto_apply_minute,
                p.auto_apply_days,
                p.last_auto_apply_at
            FROM user_preferences p
            WHERE p.auto_apply_enabled = 1
            """,
        ).fetchall()
    finally:
        db.close()

    eligible = []
    for row in rows:
        hour = int(row["auto_apply_hour"] or 9)
        minute = int(row["auto_apply_minute"] or 0)
        # Cek jam & menit cocok (toleransi 2 menit untuk handle loop 60s)
        if now.hour != hour:
            continue
        if abs(now.minute - minute) > 2:
            continue
        # Cek hari
        days = _parse_days(row["auto_apply_days"] or "")
        if weekday not in days:
            continue
        # Cek belum apply hari ini
        if _already_applied_today(row["user_id"], today):
            continue
        eligible.append({
            "user_id": row["user_id"],
            "hour": hour,
            "minute": minute,
        })
    return eligible


def _has_valid_credentials(user_id: int) -> tuple[bool, list[str]]:
    """
    Cek apakah user punya cookie file yang valid untuk minimal 1 platform.
    Returns (has_valid, list_of_invalid_platforms).
    """
    from routers.credentials import cookies_path, PLATFORM_CONFIG
    import os as _os

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT platform, COALESCE(cookie_valid, 1) AS cookie_valid
            FROM user_credentials WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    finally:
        db.close()

    if not rows:
        return False, []

    valid_platforms = []
    invalid_platforms = []
    for row in rows:
        path = cookies_path(user_id, row["platform"])
        file_exists = _os.path.exists(path)
        marked_valid = bool(row["cookie_valid"])
        if file_exists and marked_valid:
            valid_platforms.append(row["platform"])
        else:
            invalid_platforms.append(row["platform"])

    return len(valid_platforms) > 0, invalid_platforms


async def _try_auto_apply_for_user(user_id: int):
    """Mulai auto-apply session untuk user tertentu."""
    from workers.session_manager import session_manager
    from services.telegram_service import send_telegram_message, get_user_telegram

    # Cek tidak ada session aktif
    if session_manager.has_active_session(user_id):
        return

    # Cek credential valid
    has_valid, invalid_platforms = _has_valid_credentials(user_id)
    if not has_valid:
        # Kirim notifikasi (sekali per hari sudah dijaga oleh last_auto_apply_at)
        config = get_user_telegram(user_id)
        chat_id = config.get("chat_id")
        if chat_id and config.get("enabled"):
            msg = (
                "<b>⚠️ Auto-apply dilewati</b>\n"
                "Tidak ada cookie platform yang valid. "
                "Silakan upload cookie baru:\n"
                "  • /cookie linkedin\n"
                "  • /cookie jobstreet"
            )
            await send_telegram_message(chat_id, msg)
        _mark_auto_applied(user_id)  # Tandai supaya tidak retry hari ini
        return

    # Mulai session
    result = await session_manager.start_session_for_user(
        user_id=user_id,
        source="auto",
        headless_override=True,
    )

    if result.get("ok"):
        _mark_auto_applied(user_id)
    else:
        # Session gagal mulai — tetap tandai supaya tidak retry terus
        _mark_auto_applied(user_id)
        config = get_user_telegram(user_id)
        chat_id = config.get("chat_id")
        if chat_id and config.get("enabled"):
            await send_telegram_message(
                chat_id,
                f"<b>⚠️ Auto-apply gagal mulai</b>\n{result.get('message', 'Unknown error')}",
            )


async def auto_apply_scheduler_loop():
    """Loop utama scheduler. Cek setiap 60 detik."""
    while True:
        try:
            now = datetime.now(APP_TZ)
            global _last_check_minute
            # Hindari double-trigger dalam menit yang sama
            if _last_check_minute and (now - _last_check_minute).total_seconds() < 55:
                await asyncio.sleep(30)
                continue
            _last_check_minute = now

            eligible = _get_eligible_users(now)
            for user_info in eligible:
                try:
                    await _try_auto_apply_for_user(user_info["user_id"])
                except Exception as e:
                    print(f"[auto_apply_scheduler] error for user {user_info['user_id']}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[auto_apply_scheduler] loop error: {e}")

        await asyncio.sleep(60)


def start_auto_apply_scheduler():
    """Mulai scheduler sebagai background task."""
    global _scheduler_task
    loop = asyncio.get_running_loop()
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = loop.create_task(auto_apply_scheduler_loop())
        print("[auto_apply_scheduler] started")


async def stop_auto_apply_scheduler():
    """Stop scheduler (dipanggil di shutdown)."""
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
