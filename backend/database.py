import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "autoapply.db"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def ensure_column(cur, table: str, column: str, definition: str):
    cols = [row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    conn = get_db()
    cur = conn.cursor()

    # Users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT    UNIQUE NOT NULL,
            password    TEXT    NOT NULL,
            name        TEXT    NOT NULL,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)

    # Encrypted platform credentials (LinkedIn, JobStreet)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_credentials (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            platform    TEXT    NOT NULL,  -- 'linkedin' | 'jobstreet'
            email       TEXT    NOT NULL,
            password    TEXT    NOT NULL,  -- AES encrypted
            updated_at  TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, platform)
        )
    """)

    # CVs uploaded per user (each CV assigned to a position label)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cvs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            position_label TEXT    NOT NULL,
            file_name      TEXT    NOT NULL,
            file_path      TEXT    NOT NULL,
            cv_text        TEXT,            -- extracted text for Gemini
            created_at     TEXT    DEFAULT (datetime('now'))
        )
    """)

    # Job targets: combination of position + location + cv + platform
    cur.execute("""
        CREATE TABLE IF NOT EXISTS job_targets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            cv_id       INTEGER NOT NULL REFERENCES cvs(id) ON DELETE CASCADE,
            position    TEXT    NOT NULL,
            location    TEXT    NOT NULL,
            platform    TEXT    NOT NULL,   -- 'linkedin' | 'linkedin_posts' | 'jobstreet' | 'both' | 'all'
            active      INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)

    # Apply sessions — one session = one click of "Cari Kerja"
    cur.execute("""
        CREATE TABLE IF NOT EXISTS apply_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status      TEXT    DEFAULT 'running',  -- running | done | stopped
            started_at  TEXT    DEFAULT (datetime('now')),
            ended_at    TEXT
        )
    """)

    # Apply logs — one row per job applied/skipped/failed
    cur.execute("""
        CREATE TABLE IF NOT EXISTS apply_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   INTEGER NOT NULL REFERENCES apply_sessions(id) ON DELETE CASCADE,
            platform     TEXT    NOT NULL,
            job_title    TEXT,
            company      TEXT,
            job_url      TEXT,
            position     TEXT,
            location     TEXT,
            job_location TEXT,
            salary       TEXT,
            question_answers TEXT,
            confirmed_at TEXT,
            status       TEXT    DEFAULT 'applied',  -- applied | found | skipped | failed
            skip_reason  TEXT,
            applied_at   TEXT    DEFAULT (datetime('now'))
        )
    """)

    ensure_column(cur, "apply_logs", "job_location", "TEXT")
    ensure_column(cur, "apply_logs", "salary", "TEXT")
    ensure_column(cur, "apply_logs", "question_answers", "TEXT")
    ensure_column(cur, "apply_logs", "confirmed_at", "TEXT")

    # Cover letter template per target (placeholder: {perusahaan}, {posisi})
    ensure_column(cur, "job_targets", "cover_letter", "TEXT")
    ensure_column(cur, "job_targets", "employment_type", "TEXT DEFAULT 'full_time'")
    ensure_column(cur, "job_targets", "expected_salary", "TEXT DEFAULT ''")
    ensure_column(cur, "job_targets", "available_join", "TEXT DEFAULT ''")

    # Global preferences used by all targets and AI form answers.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            expected_salary TEXT DEFAULT '',
            available_join  TEXT DEFAULT '',
            headless_mode   INTEGER DEFAULT 0,
            testing_email_mode INTEGER DEFAULT 0,
            updated_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    ensure_column(cur, "user_preferences", "headless_mode", "INTEGER DEFAULT 0")
    ensure_column(cur, "user_preferences", "testing_email_mode", "INTEGER DEFAULT 0")

    # Reusable question bank. User can edit answers and the bots reuse similar ones.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS question_bank (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            platform        TEXT DEFAULT '',
            question        TEXT NOT NULL,
            normalized      TEXT NOT NULL,
            answer          TEXT NOT NULL,
            field_type      TEXT DEFAULT '',
            source          TEXT DEFAULT 'ai',
            use_count       INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, platform, normalized)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_question_bank_user_norm
        ON question_bank(user_id, normalized)
    """)

    # Telegram integration: one Telegram chat per ORDAL user.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            chat_id TEXT UNIQUE,
            link_code TEXT UNIQUE,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print("Database initialized")
