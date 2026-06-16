import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_FAILURE_PATH = os.path.join(BASE_DIR, "last_failure.json")
FAILURE_LOG_PATH = os.path.join(BASE_DIR, "failure.log")


def _compact(value, limit=2000):
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]


def log_failure(payload: dict):
    data = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        **(payload or {}),
    }
    for key in ("body_text", "modal_text", "page_text"):
        if key in data:
            data[key] = _compact(data[key], 3000)
    tmp_path = LAST_FAILURE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, LAST_FAILURE_PATH)
    with open(FAILURE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")

