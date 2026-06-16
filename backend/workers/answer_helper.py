import re
from datetime import datetime
from difflib import SequenceMatcher

from database import get_db
from workers.gemini_service import answer_question


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", (question or "").lower())).strip()


def _looks_like_salary(question: str) -> bool:
    q = normalize_question(question)
    return any(k in q for k in (
        "salary", "gaji", "compensation", "pay", "take home", "takehome",
        "remuneration", "expected monthly", "monthly base",
    ))

def _looks_like_expected_salary(question: str) -> bool:
    q = normalize_question(question)
    return _looks_like_salary(question) and any(k in q for k in (
        "expected", "expectation", "ekspektasi", "harapan", "desired", "target", "expected pay", "expected monthly",
        "salary expectation", "expected salary", "gaji yang diharapkan", "gaji harapan",
    ))

def _looks_like_current_salary(question: str) -> bool:
    q = normalize_question(question)
    return _looks_like_salary(question) and any(k in q for k in (
        "current", "present", "actual", "last", "previous", "saat ini", "sekarang", "terakhir",
        "current salary", "gaji sekarang", "gaji saat ini", "gaji terakhir",
    ))


def _looks_like_join_date(question: str) -> bool:
    q = normalize_question(question)
    return any(k in q for k in ("join", "available", "availability", "notice period", "mulai kerja", "bergabung"))

def _numeric_answer(question: str, answer: str) -> str:
    q = normalize_question(question)
    raw = (answer or "").strip().lower()
    if not raw:
        return ""
    if _looks_like_join_date(question) and any(k in q for k in ("day", "days", "calendar", "hari", "decimal", "larger than")):
        if "immediate" in raw or "segera" in raw:
            return "1"
        match = re.search(r"\d+(?:[.,]\d+)?", raw)
        if match:
            value = float(match.group(0).replace(",", "."))
            if any(k in raw for k in ("month", "bulan")):
                value *= 30
            elif any(k in raw for k in ("week", "minggu")):
                value *= 7
            return str(max(1, int(round(value))))
        return "30"
    match = re.search(r"\d+(?:[.,]\d+)?", raw.replace(".", ""))
    return match.group(0).replace(",", ".") if match else raw

def _looks_like_resume_field(question: str) -> bool:
    q = normalize_question(question)
    if q in ("cv", "resume", "curriculum vitae", "riwayat hidup"):
        return True
    return any(k in q for k in (
        "silakan pilih resume", "pilih resume", "select resume", "choose resume",
        "pilih cv", "select cv", "choose cv", "curriculum vitae", "riwayat hidup",
        "upload resume", "unggah cv",
    ))

def _looks_like_experience_years(question: str) -> bool:
    q = normalize_question(question)
    return any(k in q for k in ("experience", "pengalaman", "work experience", "years"))

def _estimate_relevant_experience_years(cv_text: str, question: str = "", job_title: str = "") -> int:
    text = cv_text or ""
    low_context = normalize_question(" ".join([question or "", job_title or "", text[:1200]]))
    purchasing_context = any(k in low_context for k in (
        "purchasing", "purchase", "procurement", "buyer", "sourcing", "supply chain", "vendor", "supplier"
    ))
    if not purchasing_context:
        return 0

    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    now = datetime.now()
    relevant_role_words = (
        "purchasing", "purchase", "procurement", "buyer", "sourcing", "supply chain", "material",
    )
    ranges = []
    pattern = re.compile(
        r"(?P<line>[^\n]{0,90}?)\b(?P<m1>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(?P<y1>20\d{2}|19\d{2})\s*[–\-]\s*(?:(?P<m2>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(?P<y2>20\d{2}|19\d{2})|(?P<present>Present|Current|Now))",
        re.I,
    )
    for match in pattern.finditer(text):
        line = normalize_question(match.group("line") or "")
        if not any(word in line for word in relevant_role_words):
            continue
        y1 = int(match.group("y1")); m1 = months[(match.group("m1") or "").lower()[:3]]
        if match.group("present"):
            y2 = now.year; m2 = now.month
        else:
            y2 = int(match.group("y2")); m2 = months[(match.group("m2") or "").lower()[:3]]
        total_months = max(0, (y2 - y1) * 12 + (m2 - m1))
        if total_months:
            ranges.append(total_months)
    years = sum(ranges) / 12 if ranges else 0
    if years <= 0:
        summary_match = re.search(r"(\d+)\+?\s+years?\s+of\s+experience", text, re.I)
        years = float(summary_match.group(1)) if summary_match else 0
    if years >= 5:
        return 5
    if years >= 4:
        return 4
    if years >= 3:
        return 3
    return max(0, int(round(years)))

def _experience_answer(question: str, field_type: str, cv_text: str, job_title: str) -> str:
    years = _estimate_relevant_experience_years(cv_text, question, job_title)
    if years <= 0:
        return ""
    q = question or ""
    q_norm = normalize_question(q)
    if field_type == "number":
        return str(years)
    if "more than 5 years" in q_norm and years >= 5:
        return "More than 5 years"
    if "lebih dari 5" in q_norm and years >= 5:
        return "Lebih dari 5 tahun"
    if "5 years" in q_norm and years >= 5:
        return "5 years"
    if "5 tahun" in q_norm and years >= 5:
        return "5 tahun"
    return f"{years} years"


def get_preferences(user_id: int) -> dict:
    db = get_db()
    row = db.execute(
        "SELECT expected_salary, available_join FROM user_preferences WHERE user_id=?",
        (user_id,),
    ).fetchone()
    db.close()
    return dict(row) if row else {"expected_salary": "", "available_join": ""}


def find_saved_answer(user_id: int, platform: str, question: str, field_type: str = ""):
    normalized = normalize_question(question)
    if not normalized:
        return None
    intent = ""
    if _looks_like_current_salary(question):
        intent = "current_salary"
    elif _looks_like_expected_salary(question):
        intent = "expected_salary"
    elif _looks_like_join_date(question):
        intent = "join_date"

    db = get_db()
    rows = db.execute(
        """
        SELECT id, question, normalized, answer
        FROM question_bank
        WHERE user_id = ? AND (platform = ? OR platform = '')
        ORDER BY platform DESC, updated_at DESC
        """,
        (user_id, platform or ""),
    ).fetchall()

    if intent:
        intent_checks = {
            "current_salary": _looks_like_current_salary,
            "expected_salary": _looks_like_expected_salary,
            "join_date": _looks_like_join_date,
        }
        for row in rows:
            if intent_checks[intent](row["question"] or row["normalized"] or ""):
                answer = row["answer"]
                db.close()
                save_question_answer(user_id, platform, question, answer, field_type, source="reused")
                return answer

    best = None
    best_score = 0.0
    for row in rows:
        saved_normalized = row["normalized"] or normalize_question(row["question"])
        score = SequenceMatcher(None, normalized, saved_normalized).ratio()
        current_tokens = set(normalized.split())
        saved_tokens = set(saved_normalized.split())
        overlap = len(current_tokens & saved_tokens) / max(1, min(len(current_tokens), len(saved_tokens)))
        score = max(score, overlap)
        if score > best_score:
            best = row
            best_score = score
    if best and best_score >= 0.72:
        db.close()
        save_question_answer(user_id, platform, question, best["answer"], field_type, source="reused")
        return best["answer"]
    db.close()
    return None


def save_question_answer(user_id: int, platform: str, question: str, answer: str, field_type: str, source="ai"):
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return
    normalized = normalize_question(question)
    db = get_db()
    db.execute(
        """
        INSERT INTO question_bank (user_id, platform, question, normalized, answer, field_type, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, platform, normalized) DO UPDATE SET
            question = excluded.question,
            answer = CASE
                WHEN question_bank.source = 'manual' AND excluded.source NOT IN ('cv', 'preference') THEN question_bank.answer
                ELSE excluded.answer
            END,
            field_type = CASE WHEN excluded.field_type != '' THEN excluded.field_type ELSE question_bank.field_type END,
            source = CASE
                WHEN question_bank.source = 'manual' AND excluded.source NOT IN ('cv', 'preference') THEN 'manual'
                ELSE excluded.source
            END,
            use_count = question_bank.use_count + 1,
            updated_at = datetime('now')
        """,
        (user_id, platform or "", question, normalized, answer, field_type or "", source),
    )
    db.commit()
    db.close()


async def answer_application_question(user_id: int, platform: str, question: str, field_type: str,
                                      cv_text: str, job_title: str, ask_user_question=None) -> str:
    if _looks_like_resume_field(question):
        return ""

    if _looks_like_experience_years(question):
        answer = _experience_answer(question, field_type, cv_text, job_title)
        if answer:
            save_question_answer(user_id, platform, question, answer, field_type, source="cv")
            return answer

    prefs = get_preferences(user_id)

    if _looks_like_expected_salary(question) and prefs.get("expected_salary"):
        answer = prefs["expected_salary"]
        save_question_answer(user_id, platform, question, answer, field_type, source="preference")
        return answer
    if _looks_like_join_date(question) and prefs.get("available_join"):
        answer = prefs["available_join"]
        if field_type == "number":
            answer = _numeric_answer(question, answer)
        save_question_answer(user_id, platform, question, answer, field_type, source="preference")
        return answer

    saved = find_saved_answer(user_id, platform, question, field_type)
    if saved:
        return _numeric_answer(question, saved) if field_type == "number" else saved

    if _looks_like_current_salary(question):
        if ask_user_question:
            answer = await ask_user_question(platform, question, field_type, job_title)
            if answer:
                save_question_answer(user_id, platform, question, answer, field_type, source="manual")
                return answer
        return ""

    if ask_user_question:
        answer = await ask_user_question(platform, question, field_type, job_title)
        if answer:
            save_question_answer(user_id, platform, question, answer, field_type, source="manual")
            return answer

    answer = await answer_question(question, field_type, cv_text, job_title)
    if answer and field_type == "number":
        answer = _numeric_answer(question, answer)
    if answer:
        save_question_answer(user_id, platform, question, answer, field_type, source="ai")
    return answer
