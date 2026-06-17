import re


TARGET_STOPWORDS = {
    "and", "dan", "di", "the", "of", "for", "with", "in", "specialist",
    "manager", "senior", "junior", "executive", "officer", "lead", "head",
    "director", "coordinator", "associate", "assistant", "analyst", "consultant",
    "supervisor", "staff", "engineer", "administrator", "member", "team",
    "person", "representative", "representatives", "associate", "officer",
}

PURCHASING_FAMILY = {
    "purchasing", "purchase", "procurement", "procure", "buyer", "buying",
    "sourcing", "pengadaan", "pembelian",
    "supply", "chain", "supplychain", "scm", "ppic", "planning", "material",
    "materials", "demand",
}

ROLE_NOISE = {
    "admin", "administrator", "administrasi", "finance", "sales", "marketing",
    "warehouse", "inventory", "stock", "gudang", "logistic", "logistics",
}

ENTRY_LEVEL = {"intern", "internship", "magang", "trainee", "apprentice", "ojt"}
JUNIOR_LEVEL = {"junior", "jr"}
SENIOR_LEVEL = {"senior", "sr", "lead", "head", "manager", "supervisor", "spv"}
CONTRACT_TERMS = {
    "contractor", "contractual", "kontrak", "pkwt", "temporary",
    "temp", "freelance", "freelancer", "projectbased", "outsourcing",
}
PART_TIME_TERMS = {"parttime", "part", "paruh"}
FULL_TIME_TERMS = {"fulltime", "full", "permanent", "tetap"}

SALES_FAMILY = {
    "sales", "selling", "seller", "account", "accounts", "keyaccount", "kam",
    "development", "bd", "commercial", "revenue", "partnership", "partnerships",
    "merchant", "retail", "store", "telesales", "telemarketing",
}
MARKETING_FAMILY = {
    "marketing", "marketer", "digital", "seo", "sem", "content", "brand",
    "branding", "social", "media", "campaign", "campaigns", "crm", "growth",
    "performance", "creative", "copywriter", "copywriting", "community",
}
FINANCE_FAMILY = {
    "finance", "financial", "accounting", "accountant", "tax", "audit",
    "auditor", "treasury", "ar", "ap", "billing", "payroll", "bookkeeping",
    "bookkeeper", "controller", "budget", "costing", "collection",
}
HR_FAMILY = {
    "hr", "human", "resources", "recruitment", "recruiter", "recruiting",
    "talent", "people", "culture", "payroll", "compensation", "benefit",
    "benefits", "ga", "affairs",
}
IT_DATA_FAMILY = {
    "software", "developer", "programmer", "frontend", "front", "backend",
    "back", "fullstack", "full", "stack", "mobile", "android", "ios", "web",
    "data", "database", "sql", "python", "java", "javascript", "devops",
    "cloud", "network", "security", "cyber", "system", "systems", "it",
    "qa", "tester", "testing", "bi", "etl", "machine", "learning", "ai",
    "intelligence", "analytics", "analytical", "dashboard", "reporting",
}
OPERATIONS_FAMILY = {
    "operation", "operations", "operational", "logistic", "logistics", "warehouse",
    "gudang", "inventory", "stock", "fulfillment", "delivery", "transport",
    "transportation", "distribution", "planner", "planning", "fleet", "export",
    "import", "exim",
}
ADMIN_FAMILY = {
    "admin", "administrator", "administration", "administrasi", "secretary",
    "sekretaris", "clerical", "office", "document", "documentation", "dataentry",
}
CUSTOMER_SERVICE_FAMILY = {
    "customer", "service", "support", "cs", "care", "call", "contact", "center",
    "centre", "helpdesk", "help", "desk", "relation", "relations", "experience",
}
LEGAL_COMPLIANCE_FAMILY = {
    "legal", "law", "lawyer", "paralegal", "compliance", "contract", "contracts",
    "license", "licensing", "risk", "regulatory", "corporate", "secretary",
}
DESIGN_FAMILY = {
    "design", "designer", "ui", "ux", "graphic", "graphics", "visual",
    "illustrator", "creative", "motion", "video", "editor", "photographer",
}
ENGINEERING_MANUFACTURING_FAMILY = {
    "manufacturing", "production", "maintenance", "mechanical", "electrical",
    "industrial", "technician", "technical", "operator", "process", "factory",
    "plant", "quality", "qc", "qa", "hse", "safety", "civil", "mep",
}
PRODUCT_PROJECT_FAMILY = {
    "product", "project", "program", "scrum", "agile", "pmo", "owner",
    "implementation",
}
HEALTH_EDUCATION_FAMILY = {
    "doctor", "dokter", "nurse", "perawat", "medical", "clinical", "pharmacy",
    "pharmacist", "teacher", "guru", "trainer", "lecturer", "instructor", "education",
}

ROLE_FAMILIES = (
    PURCHASING_FAMILY,
    SALES_FAMILY,
    MARKETING_FAMILY,
    FINANCE_FAMILY,
    HR_FAMILY,
    IT_DATA_FAMILY,
    OPERATIONS_FAMILY,
    ADMIN_FAMILY,
    CUSTOMER_SERVICE_FAMILY,
    LEGAL_COMPLIANCE_FAMILY,
    DESIGN_FAMILY,
    ENGINEERING_MANUFACTURING_FAMILY,
    PRODUCT_PROJECT_FAMILY,
    HEALTH_EDUCATION_FAMILY,
)
ALL_FAMILY_WORDS = set().union(*ROLE_FAMILIES)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", (value or "").lower())).strip()


def _tokens(value: str) -> set[str]:
    return set(normalize_text(value).split())


def _has_family(tokens: set[str], family: set[str]) -> bool:
    return any(token in family for token in tokens)

def _families_for(tokens: set[str]) -> list[set[str]]:
    return [family for family in ROLE_FAMILIES if _has_family(tokens, family)]

def _level_ok(title_tokens: set[str], target_tokens: set[str]) -> bool:
    if title_tokens & ENTRY_LEVEL and not target_tokens & ENTRY_LEVEL:
        return False
    if target_tokens & SENIOR_LEVEL:
        return bool(title_tokens & SENIOR_LEVEL) or not (title_tokens & (ENTRY_LEVEL | JUNIOR_LEVEL))
    if title_tokens & SENIOR_LEVEL and target_tokens & (ENTRY_LEVEL | JUNIOR_LEVEL):
        return False
    return True


def matches_position(text: str, position: str) -> bool:
    title_tokens = _tokens(text)
    target_tokens = _tokens(position)
    if not target_tokens:
        return True
    if not _level_ok(title_tokens, target_tokens):
        return False

    target_families = _families_for(target_tokens)
    if target_families:
        if not any(_has_family(title_tokens, family) for family in target_families):
            return False

        if _has_family(target_tokens, PURCHASING_FAMILY):
            # Allow if post contains a purchasing family word (e.g. "Admin Purchasing")
            has_purchasing_in_title = any(t in PURCHASING_FAMILY for t in title_tokens)
            if "ppic" in title_tokens and "ppic" not in target_tokens and not has_purchasing_in_title:
                return False
            if "operator" in title_tokens and "operator" not in target_tokens and not has_purchasing_in_title:
                return False
            if (title_tokens & ROLE_NOISE) and not (target_tokens & ROLE_NOISE) and not has_purchasing_in_title:
                return False

        target_core = {
            w for w in target_tokens
            if len(w) >= 3 and w not in TARGET_STOPWORDS and w not in ALL_FAMILY_WORDS
        }
        if target_core and not (target_core & title_tokens):
            return False
        return True

    words = [w for w in target_tokens if len(w) >= 4 and w not in TARGET_STOPWORDS]
    if not words:
        return True
    return any(w in title_tokens for w in words)

def matches_employment_type(text: str, employment_type: str = "full_time") -> tuple[bool, str]:
    target = normalize_text(employment_type or "full_time").replace(" ", "_")
    if target in ("", "any", "all", "semua"):
        return True, ""

    tokens = _tokens(text)
    compact = normalize_text(text).replace(" ", "")
    has_entry = bool(tokens & ENTRY_LEVEL) or any(term in compact for term in ("internship", "magang", "trainee"))
    has_contract = bool(tokens & CONTRACT_TERMS) or any(term in compact for term in ("contractbased", "contractbase", "projectbased", "pkwt"))
    has_part_time = bool(tokens & PART_TIME_TERMS) or "parttime" in compact or "parttimer" in compact
    has_full_time = bool(tokens & FULL_TIME_TERMS) or "fulltime" in compact or "fulltimer" in compact

    if target == "full_time":
        if has_entry:
            return False, "Tipe kerja intern/magang"
        if has_contract:
            return False, "Tipe kerja contract"
        if has_part_time:
            return False, "Tipe kerja part-time"
        return True, "Full-time" if has_full_time else ""

    if target == "contract":
        if has_entry:
            return False, "Tipe kerja intern/magang"
        if has_contract:
            return True, "Contract"
        return False, "Bukan contract"

    if target == "intern":
        if has_entry:
            return True, "Intern"
        return False, "Bukan intern"

    return True, ""


def parse_salary_amounts(value: str) -> list[int]:
    text = (value or "").lower()
    if not text:
        return []

    unit_million = bool(re.search(r"\b(juta|jt|million)\b", text))
    amounts: list[int] = []
    for raw in re.findall(r"\d+(?:[.,]\d+)*", text):
        cleaned = raw.strip(".,")
        if not cleaned:
            continue
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", cleaned):
            number = int(re.sub(r"[.,]", "", cleaned))
        else:
            try:
                number_float = float(cleaned.replace(",", "."))
            except ValueError:
                continue
            number = int(number_float)
            if unit_million and number_float < 1000:
                number = int(number_float * 1_000_000)
        if unit_million and number < 1000:
            number *= 1_000_000
        if number >= 100_000:
            amounts.append(number)
    return amounts


def parse_expected_salary(value: str) -> int:
    amounts = parse_salary_amounts(value)
    if amounts:
        return max(amounts)
    digits = re.sub(r"\D", "", value or "")
    return int(digits) if digits else 0


def salary_matches(expected_salary: str, salary_text: str) -> tuple[bool, str]:
    expected = parse_expected_salary(expected_salary)
    if expected <= 0:
        return True, ""
    amounts = parse_salary_amounts(salary_text)
    if not amounts:
        return True, "Gaji tidak tercantum"
    max_salary = max(amounts)
    if max_salary >= expected:
        return True, f"Gaji cocok: max Rp{max_salary:,}".replace(",", ".")
    return False, f"Gaji di bawah target: max Rp{max_salary:,}".replace(",", ".")


def get_expected_salary(user_id: int) -> str:
    try:
        from database import get_db
        db = get_db()
        row = db.execute(
            "SELECT expected_salary FROM user_preferences WHERE user_id=?",
            (user_id,),
        ).fetchone()
        db.close()
        return (row["expected_salary"] if row else "") or ""
    except Exception:
        return ""
