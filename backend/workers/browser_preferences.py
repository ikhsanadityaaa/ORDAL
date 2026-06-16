def get_headless_mode(user_id: int) -> bool:
    try:
        from database import get_db

        db = get_db()
        row = db.execute(
            "SELECT COALESCE(headless_mode, 0) AS headless_mode FROM user_preferences WHERE user_id=?",
            (user_id,),
        ).fetchone()
        db.close()
        return bool(row and row["headless_mode"])
    except Exception:
        return False

def get_testing_email_mode(user_id: int) -> bool:
    try:
        from database import get_db

        db = get_db()
        row = db.execute(
            "SELECT COALESCE(testing_email_mode, 0) AS testing_email_mode FROM user_preferences WHERE user_id=?",
            (user_id,),
        ).fetchone()
        db.close()
        return bool(row and row["testing_email_mode"])
    except Exception:
        return False
