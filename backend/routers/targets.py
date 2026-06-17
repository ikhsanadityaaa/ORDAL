from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from database import get_db
from auth_utils import get_current_user

router = APIRouter()

# 'all' = gabungan LinkedIn Jobs + LinkedIn Posts + JobStreet
VALID_PLATFORMS = ("linkedin", "linkedin_posts", "jobstreet", "both", "all")
VALID_EMPLOYMENT_TYPES = ("full_time", "contract", "intern")

class TargetRequest(BaseModel):
    cv_id: int
    positions: List[str]
    locations: List[str]
    platform: Optional[str] = None  # kompatibilitas lama
    platforms: Optional[List[str]] = None
    cover_letter: Optional[str] = None  # Template dengan {perusahaan} dan {posisi}
    employment_type: str = "full_time"
    expected_salary: Optional[str] = None
    available_join: Optional[str] = None

class CoverLetterUpdate(BaseModel):
    cover_letter: str  # Template dengan placeholder {perusahaan} dan {posisi}

class TargetUpdate(BaseModel):
    cv_id: int
    position: str
    location: str
    platform: str
    cover_letter: Optional[str] = None
    employment_type: str = "full_time"
    expected_salary: Optional[str] = None
    available_join: Optional[str] = None

def normalize_position(position: str) -> str:
    return " ".join(position.strip().lower().split())

def find_position_cover_letter(db, user_id: int, position: str):
    return db.execute(
        """
        SELECT cover_letter
        FROM job_targets
        WHERE user_id = ?
          AND lower(trim(position)) = ?
          AND cover_letter IS NOT NULL
          AND trim(cover_letter) != ''
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id, normalize_position(position))
    ).fetchone()

@router.post("/")
def create_targets(req: TargetRequest, user=Depends(get_current_user)):
    db = get_db()

    cv = db.execute("SELECT id FROM cvs WHERE id = ? AND user_id = ?", (req.cv_id, user["id"])).fetchone()
    if not cv:
        db.close()
        raise HTTPException(status_code=404, detail="CV not found")

    raw_platforms = req.platforms if req.platforms is not None else [req.platform or "all"]
    platforms = []
    for item in raw_platforms:
        platform = (item or "").strip()
        if platform and platform not in platforms:
            platforms.append(platform)

    if "all" in platforms:
        platforms = ["all"]

    if not platforms:
        db.close()
        raise HTTPException(status_code=400, detail="Pilih minimal 1 platform")

    invalid = [p for p in platforms if p not in VALID_PLATFORMS]
    if invalid:
        db.close()
        raise HTTPException(status_code=400, detail=f"Platform harus salah satu dari: {', '.join(VALID_PLATFORMS)}")

    if len(platforms) > 2:
        db.close()
        raise HTTPException(status_code=400, detail="Maksimal pilih 2 platform, atau pilih Semua")

    request_cover_letter = req.cover_letter.strip() if req.cover_letter else None
    expected_salary = (req.expected_salary or "").strip()
    available_join = (req.available_join or "").strip()
    employment_type = (req.employment_type or "full_time").strip()
    if employment_type not in VALID_EMPLOYMENT_TYPES:
        db.close()
        raise HTTPException(status_code=400, detail="Tipe kerja harus full_time, contract, atau intern")

    created = []
    for position in req.positions:
        clean_position = position.strip()
        existing_cover = find_position_cover_letter(db, user["id"], clean_position)
        cover_letter = request_cover_letter or (existing_cover["cover_letter"] if existing_cover else None)

        for location in req.locations:
            clean_location = location.strip()
            for platform in platforms:
                cur = db.execute(
                    """
                    INSERT INTO job_targets
                        (user_id, cv_id, position, location, platform, cover_letter, employment_type, expected_salary, available_join)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user["id"], req.cv_id, clean_position, clean_location, platform, cover_letter, employment_type, expected_salary, available_join)
                )
                created.append({
                    "id": cur.lastrowid,
                    "position": clean_position,
                    "location": clean_location,
                    "platform": platform,
                    "cover_letter": cover_letter,
                    "employment_type": employment_type,
                    "expected_salary": expected_salary,
                    "available_join": available_join,
                })

        if request_cover_letter:
            db.execute(
                "UPDATE job_targets SET cover_letter = ? WHERE user_id = ? AND lower(trim(position)) = ?",
                (request_cover_letter, user["id"], normalize_position(clean_position))
            )

    db.commit()
    db.close()
    return {"created": len(created), "targets": created}


@router.put("/{target_id}/cover-letter")
def update_cover_letter(target_id: int, body: CoverLetterUpdate, user=Depends(get_current_user)):
    """Update cover letter template untuk semua target dengan posisi yang sama."""
    db = get_db()
    row = db.execute(
        "SELECT id, position FROM job_targets WHERE id = ? AND user_id = ?",
        (target_id, user["id"])
    ).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Target not found")

    cover_letter = body.cover_letter.strip()
    db.execute(
        "UPDATE job_targets SET cover_letter = ? WHERE user_id = ? AND lower(trim(position)) = ?",
        (cover_letter, user["id"], normalize_position(row["position"]))
    )
    updated = db.execute("SELECT changes() AS count").fetchone()["count"]
    db.commit()
    db.close()
    return {
        "message": "Cover letter posisi diperbarui",
        "target_id": target_id,
        "position": row["position"],
        "updated": updated,
    }

@router.put("/{target_id}")
def update_target(target_id: int, req: TargetUpdate, user=Depends(get_current_user)):
    db = get_db()
    row = db.execute(
        "SELECT id FROM job_targets WHERE id = ? AND user_id = ? AND active = 1",
        (target_id, user["id"])
    ).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Target not found")

    cv = db.execute("SELECT id FROM cvs WHERE id = ? AND user_id = ?", (req.cv_id, user["id"])).fetchone()
    if not cv:
        db.close()
        raise HTTPException(status_code=404, detail="CV not found")

    position = req.position.strip()
    location = req.location.strip()
    platform = req.platform.strip()
    employment_type = (req.employment_type or "full_time").strip()
    if not position:
        db.close()
        raise HTTPException(status_code=400, detail="Posisi wajib diisi")
    if not location:
        db.close()
        raise HTTPException(status_code=400, detail="Lokasi wajib diisi")
    if platform not in VALID_PLATFORMS:
        db.close()
        raise HTTPException(status_code=400, detail=f"Platform harus salah satu dari: {', '.join(VALID_PLATFORMS)}")
    if employment_type not in VALID_EMPLOYMENT_TYPES:
        db.close()
        raise HTTPException(status_code=400, detail="Tipe kerja harus full_time, contract, atau intern")

    cover_letter = req.cover_letter.strip() if req.cover_letter else None
    expected_salary = (req.expected_salary or "").strip()
    available_join = (req.available_join or "").strip()
    db.execute(
        """
        UPDATE job_targets
        SET cv_id = ?, position = ?, location = ?, platform = ?, cover_letter = ?, employment_type = ?, expected_salary = ?, available_join = ?
        WHERE id = ? AND user_id = ?
        """,
        (req.cv_id, position, location, platform, cover_letter, employment_type, expected_salary, available_join, target_id, user["id"])
    )
    db.commit()
    updated = db.execute(
        """
        SELECT
            t.id, t.user_id, t.cv_id, t.position, t.location, t.platform, t.active,
            t.created_at, t.cover_letter, COALESCE(t.employment_type, 'full_time') AS employment_type,
            COALESCE(t.expected_salary, '') AS expected_salary,
            COALESCE(t.available_join, '') AS available_join,
            c.position_label, c.file_name
        FROM job_targets t
        JOIN cvs c ON c.id = t.cv_id
        WHERE t.id = ? AND t.user_id = ?
        """,
        (target_id, user["id"])
    ).fetchone()
    db.close()
    return dict(updated)

@router.get("/")
def list_targets(user=Depends(get_current_user)):
    db = get_db()
    rows = db.execute("""
        SELECT
            t.id,
            t.user_id,
            t.cv_id,
            t.position,
            t.location,
            t.platform,
            COALESCE(t.employment_type, 'full_time') AS employment_type,
            COALESCE(t.expected_salary, '') AS expected_salary,
            COALESCE(t.available_join, '') AS available_join,
            t.active,
            t.created_at,
            COALESCE(
                NULLIF(trim(t.cover_letter), ''),
                (
                    SELECT jt.cover_letter
                    FROM job_targets jt
                    WHERE jt.user_id = t.user_id
                      AND lower(trim(jt.position)) = lower(trim(t.position))
                      AND jt.cover_letter IS NOT NULL
                      AND trim(jt.cover_letter) != ''
                    ORDER BY jt.created_at DESC, jt.id DESC
                    LIMIT 1
                )
            ) AS cover_letter,
            c.position_label,
            c.file_name
        FROM job_targets t
        JOIN cvs c ON c.id = t.cv_id
        WHERE t.user_id = ? AND t.active = 1
        ORDER BY t.created_at DESC
    """, (user["id"],)).fetchall()
    db.close()
    return [dict(r) for r in rows]

@router.delete("/{target_id}")
def delete_target(target_id: int, user=Depends(get_current_user)):
    db = get_db()
    row = db.execute("SELECT id FROM job_targets WHERE id = ? AND user_id = ?", (target_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Target not found")
    db.execute("DELETE FROM job_targets WHERE id = ?", (target_id,))
    db.commit()
    db.close()
    return {"message": "Target deleted"}
