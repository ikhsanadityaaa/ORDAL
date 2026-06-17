import os
import re
import shutil

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_UPLOAD_DIR = os.path.join(BACKEND_DIR, "tmp_uploads")


def safe_filename(name: str) -> str:
    base = os.path.basename(name or "cv.pdf").strip() or "cv.pdf"
    base = re.sub(r"[\\/:*?\"<>|]+", "_", base)
    return base if base.lower().endswith(".pdf") else f"{base}.pdf"


def prepare_upload_file(cv_path: str, original_name: str = "") -> str:
    """Return path whose basename matches original PDF name for platform uploads."""
    if not cv_path:
        return cv_path
    if not os.path.isabs(cv_path) and not os.path.exists(cv_path):
        backend_relative = os.path.join(BACKEND_DIR, cv_path)
        if os.path.exists(backend_relative):
            cv_path = backend_relative
    wanted = safe_filename(original_name or os.path.basename(cv_path))
    if os.path.basename(cv_path) == wanted:
        return cv_path

    os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)
    target = os.path.join(TMP_UPLOAD_DIR, wanted)
    try:
        if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(cv_path):
            shutil.copy2(cv_path, target)
        return target
    except Exception:
        return cv_path
