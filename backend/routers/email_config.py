import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth_utils import get_current_user
from database import get_db

router = APIRouter()


def _ensure_email_table():
    """Pastikan tabel email_configs ada (lazy migration)."""
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_configs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            smtp_host    TEXT    NOT NULL DEFAULT 'smtp.gmail.com',
            smtp_port    INTEGER NOT NULL DEFAULT 587,
            sender_email TEXT    NOT NULL DEFAULT '',
            app_password TEXT    NOT NULL DEFAULT '',
            updated_at   TEXT    DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    db.close()


_ensure_email_table()


class EmailConfigIn(BaseModel):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_email: str
    app_password: str  # akan disimpan as-is; produksi: enkripsi


class EmailTestIn(BaseModel):
    recipient_email: Optional[str] = None


@router.get("/status")
def email_config_status(user=Depends(get_current_user)):
    """Kembalikan konfigurasi email aktif untuk user ini."""
    db = get_db()
    row = db.execute(
        "SELECT smtp_host, smtp_port, sender_email, app_password FROM email_configs WHERE user_id = ?",
        (user["id"],)
    ).fetchone()
    db.close()

    if row:
        return {
            "configured": bool(row["sender_email"] and row["app_password"]),
            "sender":     row["sender_email"],
            "smtp_host":  row["smtp_host"],
            "smtp_port":  row["smtp_port"],
        }

    # Fallback ke .env (backward compat)
    return {
        "configured": bool(os.getenv("EMAIL_SENDER") and os.getenv("EMAIL_APP_PASSWORD")),
        "sender":     os.getenv("EMAIL_SENDER", ""),
        "smtp_host":  os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port":  int(os.getenv("SMTP_PORT", 587)),
    }


@router.put("")
@router.put("/")
def save_email_config(body: EmailConfigIn, user=Depends(get_current_user)):
    """Simpan / update konfigurasi email untuk user ini."""
    db = get_db()
    db.execute("""
        INSERT INTO email_configs (user_id, smtp_host, smtp_port, sender_email, app_password, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            smtp_host    = excluded.smtp_host,
            smtp_port    = excluded.smtp_port,
            sender_email = excluded.sender_email,
            app_password = excluded.app_password,
            updated_at   = excluded.updated_at
    """, (user["id"], body.smtp_host, body.smtp_port, body.sender_email.strip(), body.app_password.strip()))
    db.commit()
    db.close()
    return {"message": "Konfigurasi email disimpan"}


@router.post("/test")
def send_test_email(body: Optional[EmailTestIn] = None, user=Depends(get_current_user)):
    """Kirim email percobaan ke email sendiri sebelum bot mengirim ke perusahaan."""
    db = get_db()
    row = db.execute(
        "SELECT smtp_host, smtp_port, sender_email, app_password FROM email_configs WHERE user_id = ?",
        (user["id"],)
    ).fetchone()
    db.close()

    if row:
        smtp_host = row["smtp_host"]
        smtp_port = int(row["smtp_port"])
        sender_email = row["sender_email"]
        app_password = row["app_password"]
    else:
        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        sender_email = os.getenv("EMAIL_SENDER", "")
        app_password = os.getenv("EMAIL_APP_PASSWORD", "")

    if not sender_email or not app_password:
        raise HTTPException(status_code=400, detail="Email belum dikonfigurasi. Isi Email Pengirim dan App Password dulu.")

    recipient_email = (body.recipient_email if body else None) or sender_email
    recipient_email = recipient_email.strip()
    if not recipient_email:
        raise HTTPException(status_code=400, detail="Email tujuan test wajib diisi.")

    from workers.linkedin_posts_bot import send_email

    success, err = send_email(
        smtp_host,
        smtp_port,
        sender_email,
        app_password,
        recipient_email,
        "Test Email ORDAL",
        "Ini email test dari ORDAL. Jika email ini masuk, konfigurasi SMTP sudah siap sebelum kirim ke email perusahaan.",
    )
    if not success:
        raise HTTPException(status_code=400, detail=f"Gagal kirim email test: {err}")
    return {"message": f"Email test terkirim ke {recipient_email}"}


@router.delete("")
@router.delete("/")
def delete_email_config(user=Depends(get_current_user)):
    """Hapus konfigurasi email user ini."""
    db = get_db()
    db.execute("DELETE FROM email_configs WHERE user_id = ?", (user["id"],))
    db.commit()
    db.close()
    return {"message": "Konfigurasi email dihapus"}
