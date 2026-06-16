from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth_utils import get_current_user
from database import get_db

router = APIRouter()


class PreferenceUpdate(BaseModel):
    expected_salary: str = ""
    available_join: str = ""
    headless_mode: bool = False
    testing_email_mode: bool = False


@router.get("/")
def get_preferences(user=Depends(get_current_user)):
    db = get_db()
    row = db.execute(
        """
        SELECT expected_salary, available_join,
               COALESCE(headless_mode, 0) AS headless_mode,
               COALESCE(testing_email_mode, 0) AS testing_email_mode
        FROM user_preferences
        WHERE user_id = ?
        """,
        (user["id"],),
    ).fetchone()
    db.close()
    if not row:
        return {"expected_salary": "", "available_join": "", "headless_mode": False, "testing_email_mode": False}
    data = dict(row)
    data["headless_mode"] = bool(data.get("headless_mode"))
    data["testing_email_mode"] = bool(data.get("testing_email_mode"))
    return data


@router.put("/")
def update_preferences(body: PreferenceUpdate, user=Depends(get_current_user)):
    expected_salary = (body.expected_salary or "").strip()
    available_join = (body.available_join or "").strip()
    headless_mode = 1 if body.headless_mode else 0
    testing_email_mode = 1 if body.testing_email_mode else 0
    db = get_db()
    db.execute(
        """
        INSERT INTO user_preferences (user_id, expected_salary, available_join, headless_mode, testing_email_mode, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            expected_salary = excluded.expected_salary,
            available_join = excluded.available_join,
            headless_mode = excluded.headless_mode,
            testing_email_mode = excluded.testing_email_mode,
            updated_at = datetime('now')
        """,
        (user["id"], expected_salary, available_join, headless_mode, testing_email_mode),
    )
    db.commit()
    db.close()
    return {
        "expected_salary": expected_salary,
        "available_join": available_join,
        "headless_mode": bool(headless_mode),
        "testing_email_mode": bool(testing_email_mode),
    }
