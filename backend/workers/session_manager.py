import asyncio
import sys
import threading
import os
import uuid
import re
from typing import Dict
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ordal_debug.log")

def _log(msg: str):
    """Tulis log ke file dan stdout untuk debugging."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


class SessionManager:
    def __init__(self):
        self._queues:     Dict[int, asyncio.Queue]       = {}
        self._threads:    Dict[int, threading.Thread]    = {}
        self._stop_flags: Dict[int, threading.Event]     = {}
        self._pending_questions: Dict[int, dict] = {}
        self._event_history: Dict[int, list] = {}

    async def start_session(self, session_id: int, user_id: int, targets: list, credentials: list,
                            source: str = "manual", chat_id: str | None = None):
        self._queues[user_id]     = asyncio.Queue()
        self._stop_flags[user_id] = threading.Event()
        self._event_history[user_id] = []
        main_loop = asyncio.get_running_loop()

        def thread_target():
            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self._run_bots(session_id, user_id, targets, main_loop, source=source, chat_id=chat_id)
                )
            except Exception as e:
                try:
                    from failure_logger import log_failure
                    log_failure({"platform": "session", "step": "thread_crash", "reason": str(e), "session_id": session_id})
                except Exception:
                    pass
                asyncio.run_coroutine_threadsafe(
                    self._put(user_id, {"type": "error", "message": str(e)}), main_loop
                )
            finally:
                loop.close()

        t = threading.Thread(target=thread_target, daemon=True)
        self._threads[user_id] = t
        t.start()

    async def stop_session(self, user_id: int):
        if user_id in self._stop_flags:
            self._stop_flags[user_id].set()
        for record in list(self._pending_questions.get(user_id, {}).values()):
            try:
                record["answer"] = ""
                record["event"].set()
            except Exception:
                pass
        self._pending_questions.pop(user_id, None)
        await self._put(user_id, {"type": "done", "reason": "stopped"})

    def has_active_session(self, user_id: int) -> bool:
        thread = self._threads.get(user_id)
        return bool(thread and thread.is_alive())

    async def start_session_for_user(
        self,
        user_id: int,
        source: str = "manual",
        headless_override: bool | None = None,
    ) -> dict:
        """
        Entry point yang bisa dipanggil dari router HTTP ATAU scheduler tanpa HTTP context.
        Mengembalikan dict: {"ok": bool, "session_id": int|None, "message": str}.

        - source: 'manual' (dari /apply) atau 'auto' (dari scheduler)
        - headless_override: True/False untuk paksa mode headless (untuk auto-apply di VPS)
        """
        from database import get_db

        db = get_db()
        try:
            # 1. Cek session running yang masih hidup
            running = db.execute(
                "SELECT id FROM apply_sessions WHERE user_id = ? AND status = 'running'",
                (user_id,),
            ).fetchone()
            if running:
                if self.has_active_session(user_id):
                    return {"ok": False, "session_id": None, "message": "Sesi lain masih berjalan."}
                db.execute(
                    "UPDATE apply_sessions SET status='stopped', ended_at=datetime('now') WHERE id=?",
                    (running["id"],),
                )
                db.commit()

            # 2. Load active targets
            targets = db.execute(
                """
                SELECT
                    t.id, t.user_id, t.cv_id, t.position, t.location, t.platform,
                    COALESCE(t.employment_type, 'full_time') AS employment_type,
                    COALESCE(t.expected_salary, '') AS expected_salary,
                    COALESCE(t.available_join, '') AS available_join,
                    t.active, t.created_at,
                    COALESCE(
                        NULLIF(trim(t.cover_letter), ''),
                        (
                            SELECT jt.cover_letter FROM job_targets jt
                            WHERE jt.user_id = t.user_id
                              AND lower(trim(jt.position)) = lower(trim(t.position))
                              AND jt.cover_letter IS NOT NULL AND trim(jt.cover_letter) != ''
                            ORDER BY jt.created_at DESC, jt.id DESC LIMIT 1
                        )
                    ) AS cover_letter,
                    c.file_path, c.file_name, c.cv_text
                FROM job_targets t JOIN cvs c ON c.id = t.cv_id
                WHERE t.user_id = ? AND t.active = 1
                """,
                (user_id,),
            ).fetchall()
            if not targets:
                return {"ok": False, "session_id": None, "message": "Belum ada target aktif. Tambahkan via /target_add."}

            # 3. Load credentials (marker rows)
            creds = db.execute(
                "SELECT platform, email, password FROM user_credentials WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            if not creds:
                return {"ok": False, "session_id": None, "message": "Belum ada credentials. Upload cookie via /cookie."}

            # 4. Insert session row
            cur = db.execute(
                "INSERT INTO apply_sessions (user_id, status, source) VALUES (?, 'running', ?)",
                (user_id, source),
            )
            db.commit()
            session_id = cur.lastrowid
        finally:
            db.close()

        # 5. Dedupe targets
        deduped = []
        seen = set()
        for t in [dict(r) for r in targets]:
            key = (
                (t.get("platform") or "").strip().lower(),
                (t.get("position") or "").strip().lower(),
                (t.get("location") or "").strip().lower(),
                (t.get("employment_type") or "full_time").strip().lower(),
                t.get("cv_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(t)

        # 6. Apply headless override ke user_preferences (sementara, dipulihkan setelah session)
        if headless_override is not None:
            try:
                db = get_db()
                db.execute(
                    """
                    INSERT INTO user_preferences (user_id, headless_mode, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(user_id) DO UPDATE SET headless_mode = excluded.headless_mode, updated_at = datetime('now')
                    """,
                    (user_id, 1 if headless_override else 0),
                )
                db.commit()
                db.close()
            except Exception as e:
                _log(f"headless override error: {e}")

        # 7. Start session
        # Ambil chat_id user untuk notifikasi auto-apply
        chat_id = None
        try:
            db = get_db()
            trow = db.execute(
                "SELECT chat_id FROM telegram_users WHERE user_id = ? AND enabled = 1",
                (user_id,),
            ).fetchone()
            db.close()
            if trow:
                chat_id = trow["chat_id"]
        except Exception:
            pass

        await self.start_session(
            session_id=session_id,
            user_id=user_id,
            targets=deduped,
            credentials=[dict(c) for c in creds],
            source=source,
            chat_id=chat_id,
        )
        return {"ok": True, "session_id": session_id, "message": "Sesi dimulai"}

    def get_session_status(self, user_id: int) -> dict:
        """Status session aktif untuk user (dipakai oleh /status di Telegram)."""
        from database import get_db
        db = get_db()
        try:
            session = db.execute(
                """
                SELECT id, status, source, started_at, ended_at
                FROM apply_sessions
                WHERE user_id = ? AND status = 'running'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if not session:
                return {"running": False}
            counts = db.execute(
                """
                SELECT platform, status, COUNT(*) AS n
                FROM apply_logs
                WHERE session_id = ?
                GROUP BY platform, status
                """,
                (session["id"],),
            ).fetchall()
            total_applied = sum(r["n"] for r in counts if r["status"] == "applied")
            total_skipped = sum(r["n"] for r in counts if r["status"] == "skipped")
            total_failed = sum(r["n"] for r in counts if r["status"] == "failed")
            return {
                "running": True,
                "session_id": session["id"],
                "source": session["source"],
                "started_at": session["started_at"],
                "applied": total_applied,
                "skipped": total_skipped,
                "failed": total_failed,
            }
        finally:
            db.close()

    def get_pending_question(self, user_id: int, prompt_id: str) -> dict | None:
        return self._pending_questions.get(user_id, {}).get(prompt_id)

    def latest_pending_question(self, user_id: int):
        pending = self._pending_questions.get(user_id, {})
        if not pending:
            return None
        prompt_id = next(reversed(pending))
        return prompt_id, pending[prompt_id]

    def session_should_stop(self, session_id: int, stop_flag: threading.Event) -> bool:
        if stop_flag.is_set():
            return True
        try:
            from database import get_db
            db = get_db()
            row = db.execute("SELECT status FROM apply_sessions WHERE id=?", (session_id,)).fetchone()
            db.close()
            return bool(row and row["status"] != "running")
        except Exception:
            return stop_flag.is_set()

    async def subscribe(self, user_id: int) -> asyncio.Queue:
        if user_id not in self._queues:
            self._queues[user_id] = asyncio.Queue()
        queue = self._queues[user_id]
        for event in self._event_history.get(user_id, [])[-120:]:
            await queue.put(event)
        for prompt_id, record in self._pending_questions.get(user_id, {}).items():
            event = record.get("prompt")
            if event:
                await queue.put(event)
        return queue

    async def unsubscribe(self, user_id: int):
        self._queues.pop(user_id, None)

    async def _put(self, user_id: int, event: dict):
        if event.get("type") != "heartbeat":
            history = self._event_history.setdefault(user_id, [])
            history.append(event)
            del history[:-200]
        if user_id in self._queues:
            await self._queues[user_id].put(event)

    def _put_threadsafe(self, user_id: int, event: dict, main_loop: asyncio.AbstractEventLoop):
        """Thread-safe emit dari bot thread ke main uvicorn loop."""
        asyncio.run_coroutine_threadsafe(self._put(user_id, event), main_loop)

    async def ask_user_question(self, user_id: int, platform: str, question: str,
                                field_type: str, job_title: str,
                                main_loop: asyncio.AbstractEventLoop) -> str:
        prompt_id = uuid.uuid4().hex
        waiter = threading.Event()
        record = {"event": waiter, "answer": ""}
        self._pending_questions.setdefault(user_id, {})[prompt_id] = record
        try:
            from services.telegram_service import parse_prompt_options, prompt_kind
            options = parse_prompt_options(question)
            answer_mode = prompt_kind(field_type, question)
        except Exception:
            options = []
            answer_mode = field_type or "text"
        prompt_event = {
            "type": "question_prompt",
            "prompt_id": prompt_id,
            "platform": platform,
            "question": question,
            "field_type": field_type,
            "answer_mode": answer_mode,
            "options": options,
            "job_title": job_title,
        }
        record["prompt"] = prompt_event
        self._put_threadsafe(user_id, prompt_event, main_loop)
        try:
            from services.telegram_service import send_question_to_telegram
            asyncio.run_coroutine_threadsafe(send_question_to_telegram(user_id, prompt_event), main_loop)
        except Exception:
            pass

        ok = await asyncio.to_thread(waiter.wait, 600)
        self._pending_questions.get(user_id, {}).pop(prompt_id, None)
        if not ok:
            self._put_threadsafe(user_id, {
                "type": "status",
                "platform": platform,
                "message": f"Pertanyaan dilewati karena tidak dijawab: {question[:80]}",
            }, main_loop)
            return ""
        return (record.get("answer") or "").strip()

    async def answer_question_prompt(self, user_id: int, prompt_id: str, answer: str) -> bool:
        record = self._pending_questions.get(user_id, {}).get(prompt_id)
        if not record:
            return False
        record["answer"] = (answer or "").strip()
        record["event"].set()
        return True

    async def _run_bots(self, session_id: int, user_id: int, targets: list,
                        main_loop: asyncio.AbstractEventLoop,
                        source: str = "manual", chat_id: str | None = None):
        from workers.linkedin_bot import LinkedInBot
        from workers.linkedin_posts_bot import LinkedInPostsBot
        from workers.jobstreet_bot import JobStreetBot
        from workers.answer_helper import set_auto_mode
        from database import get_db

        is_auto = (source == "auto")
        cookie_expired_platforms: set[str] = set()

        # ── Set auto-apply mode untuk answer_helper ──
        if is_auto and chat_id:
            set_auto_mode(user_id, main_loop=main_loop, chat_id=chat_id)

        linkedin_targets      = [t for t in targets if t["platform"] in ("linkedin", "both", "all")]
        linkedin_post_targets = [t for t in targets if t["platform"] in ("linkedin_posts", "all")]
        jobstreet_targets     = [t for t in targets if t["platform"] in ("jobstreet", "both", "all")]
        _log(f"session={session_id} source={source} LI:{len(linkedin_targets)} LIP:{len(linkedin_post_targets)} JS:{len(jobstreet_targets)}")

        stop_flag = self._stop_flags.get(user_id, threading.Event())
        should_stop = lambda: self.session_should_stop(session_id, stop_flag)

        # ── Kirim notifikasi start untuk auto-apply ──
        if is_auto and chat_id:
            try:
                from services.telegram_service import send_telegram_message
                import html as _html
                start_msg = (
                    "<b>🚀 Auto-apply dimulai</b>\n"
                    f"Session ID: <code>{session_id}</code>\n"
                    f"Targets: LinkedIn({len(linkedin_targets)}), LinkedIn Posts({len(linkedin_post_targets)}), JobStreet({len(jobstreet_targets)})\n"
                    "Bot sedang berjalan di background. Anda akan dapat notifikasi saat selesai."
                )
                asyncio.run_coroutine_threadsafe(
                    send_telegram_message(chat_id, start_msg), main_loop
                )
            except Exception:
                pass

        async def log_apply(
            platform, job_title, company, job_url,
            position, location, status, skip_reason=None,
            job_location=None, salary=None, question_answers=None,
        ):
            _log(f"APPLY {platform} [{status}] {job_title} @ {company} reason={skip_reason}")
            should_emit = True
            try:
                db = get_db()
                existing = db.execute(
                    """
                    SELECT id, status FROM apply_logs
                    WHERE session_id = ? AND platform = ?
                      AND (
                        (job_url IS NOT NULL AND job_url != '' AND job_url = ?)
                        OR (lower(trim(COALESCE(job_title, ''))) = ? AND lower(trim(COALESCE(company, ''))) = ?)
                      )
                    ORDER BY id DESC LIMIT 1
                    """,
                    (session_id, platform, job_url or "", _norm(job_title), _norm(company)),
                ).fetchone()
                if existing:
                    if status == "applied" and existing["status"] != "applied":
                        db.execute(
                            """
                            UPDATE apply_logs
                            SET job_title=?, company=?, job_url=?, position=?, location=?, job_location=?, salary=?,
                                question_answers=?, confirmed_at=datetime('now'), status='applied', skip_reason=NULL
                            WHERE id=?
                            """,
                            (job_title, company, job_url, position, location, job_location, salary, question_answers, existing["id"]),
                        )
                        db.commit()
                    else:
                        should_emit = False
                    db.close()
                else:
                    db.execute("""
                    INSERT INTO apply_logs
                        (session_id, platform, job_title, company, job_url,
                         position, location, job_location, salary,
                         question_answers, confirmed_at, status, skip_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            CASE WHEN ? = 'applied' THEN datetime('now') ELSE NULL END,
                            ?, ?)
                """, (
                    session_id, platform, job_title, company, job_url,
                    position, location, job_location, salary,
                    question_answers, status, status, skip_reason
                    ))
                    db.commit()
                    db.close()
            except Exception as e:
                _log(f"log_apply DB error: {e}")

            # Kirim SEMUA status via SSE (bukan hanya applied/found)
            if not should_emit:
                return
            event_type = status if status in ("applied", "found", "skipped", "failed") else "skipped"
            self._put_threadsafe(user_id, {
                "type": event_type, "platform": platform,
                "job_title": job_title, "company": company, "job_url": job_url,
                "position": position, "location": location,
                "job_location": job_location, "salary": salary,
                "question_answers": question_answers,
                "skip_reason": skip_reason,
            }, main_loop)

        # ── KRITIS: emit dari bot harus pakai _put_threadsafe ──
        def sync_emit(event: dict):
            try:
                event_type = event.get("type")
                platform = event.get("platform") or "session"
                # Intercept cookie_expired events
                if event_type == "cookie_expired" and platform:
                    cookie_expired_platforms.add(platform)
                    _log(f"COOKIE EXPIRED: {platform}")
                    # Update DB: mark credential as invalid + set warning timestamp
                    try:
                        from database import get_db as _gdb
                        _db = _gdb()
                        _db.execute(
                            """
                            UPDATE user_credentials
                            SET cookie_valid = 0, last_cookie_warning_at = datetime('now')
                            WHERE user_id = ? AND platform = ?
                            """,
                            (user_id, platform),
                        )
                        _db.commit()
                        _db.close()
                    except Exception as ce:
                        _log(f"cookie_expired DB update error: {ce}")
                if event_type in ("status", "error"):
                    _log(f"EVENT {platform} {event_type}: {(event.get('message') or '')[:220]}")
                elif event_type == "progress":
                    _log(
                        f"PROGRESS {platform} {event.get('step')}/{event.get('status')} "
                        f"{(event.get('job_title') or '')[:80]} msg={(event.get('message') or '')[:120]}"
                    )
            except Exception:
                pass
            self._put_threadsafe(user_id, event, main_loop)

        async def ask_question(platform: str, question: str, field_type: str, job_title: str) -> str:
            return await self.ask_user_question(user_id, platform, question, field_type, job_title, main_loop)

        try:
            # LinkedIn Jobs
            if linkedin_targets and not stop_flag.is_set():
                _log("LinkedInBot START")
                try:
                    bot = LinkedInBot(user_id=user_id, on_apply=log_apply, emit=sync_emit, ask_user_question=ask_question, should_stop=should_stop)
                    await bot.run(linkedin_targets)
                    _log("LinkedInBot DONE")
                except asyncio.CancelledError:
                    _log("LinkedInBot cancelled")
                    raise
                except Exception as e:
                    _log(f"LinkedInBot crash: {e}")
                    try:
                        from failure_logger import log_failure
                        log_failure({"platform": "linkedin", "step": "bot_crash", "reason": str(e)})
                    except Exception:
                        pass
                    self._put_threadsafe(user_id, {"type": "error", "platform": "linkedin", "message": f"LinkedInBot: {e}"}, main_loop)

            # LinkedIn Posts
            if linkedin_post_targets and not stop_flag.is_set():
                try:
                    bot = LinkedInPostsBot(user_id=user_id, on_apply=log_apply, emit=sync_emit, should_stop=should_stop)
                    await bot.run(linkedin_post_targets)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _log(f"LinkedInPostsBot crash: {e}")
                    try:
                        from failure_logger import log_failure
                        log_failure({"platform": "linkedin_posts", "step": "bot_crash", "reason": str(e)})
                    except Exception:
                        pass
                    self._put_threadsafe(user_id, {"type": "error", "platform": "linkedin_posts", "message": f"LinkedInPostsBot: {e}"}, main_loop)

            # JobStreet
            if jobstreet_targets and not stop_flag.is_set():
                try:
                    bot = JobStreetBot(user_id=user_id, on_apply=log_apply, emit=sync_emit, ask_user_question=ask_question, should_stop=should_stop)
                    await bot.run(jobstreet_targets)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _log(f"JobStreetBot crash: {e}")
                    try:
                        from failure_logger import log_failure
                        log_failure({"platform": "jobstreet", "step": "bot_crash", "reason": str(e)})
                    except Exception:
                        pass
                    self._put_threadsafe(user_id, {"type": "error", "platform": "jobstreet", "message": f"JobStreetBot: {e}"}, main_loop)

        except asyncio.CancelledError:
            pass
        finally:
            # ── Clear auto-apply mode ──
            try:
                set_auto_mode(user_id)  # clear
            except Exception:
                pass

            # ── Finalize session di DB ──
            try:
                db = get_db()
                db.execute(
                    """
                    UPDATE apply_sessions
                    SET status='done', ended_at=datetime('now')
                    WHERE id=? AND status='running'
                    """,
                    (session_id,)
                )
                db.commit()
                db.close()
            except Exception as e:
                _log(f"session finalize DB error: {e}")

            # ── Kirim notifikasi end untuk auto-apply ──
            if is_auto and chat_id:
                try:
                    from services.telegram_service import send_telegram_message
                    from database import get_db as _gdb2
                    _db2 = _gdb2()
                    counts = _db2.execute(
                        """
                        SELECT status, COUNT(*) AS n
                        FROM apply_logs
                        WHERE session_id = ?
                        GROUP BY status
                        """,
                        (session_id,),
                    ).fetchall()
                    _db2.close()
                    applied = sum(r["n"] for r in counts if r["status"] == "applied")
                    skipped = sum(r["n"] for r in counts if r["status"] == "skipped")
                    failed = sum(r["n"] for r in counts if r["status"] == "failed")

                    lines = [
                        "<b>✅ Auto-apply selesai</b>",
                        f"Session ID: <code>{session_id}</code>",
                        f"Berhasil apply: <b>{applied}</b>",
                        f"Dilewati: {skipped}",
                        f"Gagal: {failed}",
                    ]
                    if cookie_expired_platforms:
                        lines.append("")
                        lines.append("⚠️ <b>Cookie expired:</b>")
                        for p in sorted(cookie_expired_platforms):
                            lines.append(f"  • {p.upper()} — silakan upload cookie baru via /cookie {p}")
                    end_msg = "\n".join(lines)
                    asyncio.run_coroutine_threadsafe(
                        send_telegram_message(chat_id, end_msg), main_loop
                    )
                except Exception as e:
                    _log(f"end notification error: {e}")

            self._put_threadsafe(user_id, {"type": "done"}, main_loop)



session_manager = SessionManager()
