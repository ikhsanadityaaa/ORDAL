import os
import re
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from database import get_db
from auth_utils import get_current_user

router = APIRouter()

UPLOAD_DIR = "uploads/cvs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def safe_filename(name: str) -> str:
    base = os.path.basename(name or "cv.pdf").strip() or "cv.pdf"
    base = re.sub(r"[\\/:*?\"<>|]+", "_", base)
    return base if base.lower().endswith(".pdf") else f"{base}.pdf"

def extract_pdf_text(file_path: str) -> str:
    """Extract text from PDF for Gemini context."""
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        return ""

@router.post("/upload")
async def upload_cv(
    position_label: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(get_current_user)
):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Save with original filename under user folder so platform upload name matches PDF name.
    original_name = safe_filename(file.filename)
    user_dir = os.path.join(UPLOAD_DIR, str(user["id"]))
    os.makedirs(user_dir, exist_ok=True)
    file_path = os.path.join(user_dir, original_name)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # Extract text
    cv_text = extract_pdf_text(file_path)

    # Save to DB
    db = get_db()
    cur = db.execute(
        "INSERT INTO cvs (user_id, position_label, file_name, file_path, cv_text) VALUES (?, ?, ?, ?, ?)",
        (user["id"], position_label, file.filename, file_path, cv_text)
    )
    db.commit()
    cv_id = cur.lastrowid
    db.close()

    return {
        "id": cv_id,
        "position_label": position_label,
        "file_name": file.filename,
        "file_url": f"/uploads/cvs/{user['id']}/{original_name}",
        "has_text": bool(cv_text)
    }

@router.get("/")
def list_cvs(user=Depends(get_current_user)):
    db = get_db()
    rows = db.execute(
        "SELECT id, position_label, file_name, file_path, created_at FROM cvs WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]

@router.delete("/{cv_id}")
def delete_cv(cv_id: int, user=Depends(get_current_user)):
    db = get_db()
    row = db.execute("SELECT * FROM cvs WHERE id = ? AND user_id = ?", (cv_id, user["id"])).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="CV not found")

    # Delete file from disk
    try:
        os.remove(row["file_path"])
    except FileNotFoundError:
        pass

    db.execute("DELETE FROM job_targets WHERE cv_id = ? AND user_id = ?", (cv_id, user["id"]))
    db.execute("DELETE FROM cvs WHERE id = ?", (cv_id,))
    db.commit()
    db.close()
    return {"message": "CV deleted"}
