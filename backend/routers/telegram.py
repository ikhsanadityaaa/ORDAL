from fastapi import APIRouter, Depends

from auth_utils import get_current_user
from services.telegram_service import (
    format_application_report,
    get_or_create_link_code,
    get_user_telegram,
    telegram_available,
)

router = APIRouter()


@router.get("/status")
def telegram_status(user=Depends(get_current_user)):
    code = get_or_create_link_code(user["id"])
    cfg = get_user_telegram(user["id"])
    return {
        "bot_configured": telegram_available(),
        "connected": bool(cfg.get("chat_id")),
        "enabled": bool(cfg.get("enabled")),
        "link_code": code,
        "start_command": f"/start {code}",
        "note": "Kirim start_command ke bot Telegram Anda untuk menghubungkan akun.",
    }


@router.get("/report")
def telegram_report(today_only: bool = True, user=Depends(get_current_user)):
    return {"message": format_application_report(user["id"], today_only=today_only)}
