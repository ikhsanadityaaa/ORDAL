"""
JobStreet Bot v3 — all bugs fixed
Fixes:
1. cover_template properly passed to _fill_and_submit
2. matches_position: ANY word match (was ALL)
3. pagination: try multiple selectors + fallback
4. duplicate tracking stops at 10 consecutive
5. NameError cover_template eliminated
"""
import asyncio
import os
import random
import re
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from workers.browser_launcher import launch_browser

from workers.gemini_service import generate_cover_letter, render_cover_letter_template
from workers.answer_helper import answer_application_question, save_question_answer
from workers.browser_preferences import get_headless_mode
from workers.file_utils import prepare_upload_file, safe_filename
from workers.match_utils import get_expected_salary, matches_employment_type, matches_position as position_matches, parse_expected_salary, parse_salary_amounts, salary_matches
from failure_logger import log_failure

COOKIES_DIR               = "cookies"
USER_AGENT                = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
JOBSTREET_HOME            = "https://id.jobstreet.com/id"
JOBSTREET_COOKIE_URLS     = ["https://id.jobstreet.com", "https://www.jobstreet.com", "https://seek.com"]
JOBSTREET_SESSION_COOKIES = ["SEEK_AU_AUTH", "JobseekerSessionToken", "id_token", "seekSessionToken"]
ANONYMOUS_COMPANY_TERMS   = {"pengiklan anonim", "anonymous advertiser", "confidential company"}
JOBSTREET_LOGGED_IN_SELECTORS = [
    "button:has(img)", "button[aria-label*='profile' i]",
    "[data-automation*='profile' i]", "[data-automation*='account' i]",
    "a[href*='/profile']", "a[href*='/id/profile']",
]

TARGET_STOPWORDS = {"and","dan","di","the","of","for","with","in","specialist","manager","senior","junior","executive","officer","lead","head","director","coordinator","associate","assistant","analyst","consultant","supervisor","staff","engineer","administrator"}

LOCATION_ALIASES: dict = {
    "bekasi":     ["bekasi", "cikarang"],
    "jakarta":    ["jakarta pusat","jakarta selatan","jakarta utara","jakarta barat","jakarta timur","jakarta raya","dki jakarta","daerah khusus ibukota jakarta","greater jakarta","jakarta"],
    "bandung":    ["bandung", "cimahi"],
    "surabaya":   ["surabaya", "sidoarjo"],
    "tangerang":  ["tangerang", "tangerang selatan", "bsd"],
    "depok":      ["depok"],
    "bogor":      ["bogor"],
    "medan":      ["medan"],
    "semarang":   ["semarang"],
    "yogyakarta": ["yogyakarta", "sleman", "bantul"],
    "karawang":   ["karawang"],
}

PROVINCE_NOISE = [
    "jawa barat","jawa tengah","jawa timur","banten","dki jakarta",
    "jawa","barat","timur","tengah","raya",
]

def storage_state_path(user_id, platform):
    return os.path.join(COOKIES_DIR, f"{user_id}_{platform}.json")

def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", (value or "").lower())).strip()

def strip_province(location_text: str) -> str:
    normalized = normalize_text(location_text)
    for noise in PROVINCE_NOISE:
        normalized = normalized.replace(noise, " ").strip()
    return re.sub(r"\s+", " ", normalized).strip()

def matches_location(job_location_raw: str, target_location: str) -> bool:
    if not job_location_raw:
        return True
    raw_clean  = normalize_text(job_location_raw)
    cleaned    = strip_province(job_location_raw)
    target_key = normalize_text(target_location)
    accepted = LOCATION_ALIASES.get(target_key, [target_key])
    if target_key in raw_clean or target_key in cleaned:
        return True
    return any(alias in raw_clean or alias in cleaned for alias in accepted)

def matches_position(text: str, position: str) -> bool:
    return position_matches(text, position)

async def safe_text(page_or_el, selector: str = None) -> str:
    try:
        el = await page_or_el.query_selector(selector) if selector else page_or_el
        return (await el.inner_text()).strip() if el else ""
    except Exception:
        return ""

def first_nonempty(*values) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""

def clean_salary(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    lowered = text.lower()
    if not text:
        return ""
    if len(text) > 180:
        return ""
    if "salary type" in lowered or ("min" in lowered and "maks" in lowered):
        return ""
    return text

def is_resume_field(label: str) -> bool:
    text = normalize_text(label)
    return any(part in text for part in (
        "resume", "cv", "curriculum vitae", "riwayat hidup",
        "silakan pilih resume", "pilih resume", "pilih cv", "select resume", "select cv",
    ))

def is_jobstreet_url(value: str) -> bool:
    try:
        host = urlparse(value or "").netloc.lower()
        return (
            host == "jobstreet.com" or host.endswith(".jobstreet.com") or
            host == "seek.com" or host.endswith(".seek.com")
        )
    except Exception:
        return False

def clean_company_name(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return "" if text.lower() in ANONYMOUS_COMPANY_TERMS else text

def company_for_cover(value: str) -> str:
    return clean_company_name(value)

def is_placeholder_option(value: str) -> bool:
    text = normalize_text(value)
    return not text or text in {
        "select", "choose", "pilih", "silakan pilih", "silakan buat seleksi",
        "please select", "make a selection", "buat seleksi",
    }

def choose_jobstreet_option(question: str, option_texts: list[str], expected_salary: str = "") -> str:
    q = normalize_text(question)
    pairs = [(raw, normalize_text(raw)) for raw in option_texts]
    usable = [(raw, norm) for raw, norm in pairs if not is_placeholder_option(raw)]
    if not usable:
        return ""
    salary_options = []
    for raw, _ in usable:
        amounts = parse_salary_amounts(raw)
        if amounts:
            salary_options.append((raw, max(amounts)))

    def pick(*needles: str) -> str:
        normalized_needles = [normalize_text(n) for n in needles]
        for raw, norm in usable:
            if any(norm == n or n in norm for n in normalized_needles):
                return raw
        return ""

    if "salary" in q or "gaji" in q or len(salary_options) >= 2 or any("rp 15 million" == norm for _, norm in usable):
        expected = parse_expected_salary(expected_salary)
        if expected and salary_options:
            at_or_above = [(raw, amount) for raw, amount in salary_options if amount >= expected]
            if at_or_above:
                return min(at_or_above, key=lambda item: item[1])[0]
            return min(salary_options, key=lambda item: abs(item[1] - expected))[0]
        return pick("Rp 15 million", "15 million") or (salary_options[-1][0] if salary_options else "")
    if "qualification" in q or "kualifikasi" in q or any("bachelor degree s1" == norm for _, norm in usable):
        return pick("Bachelor Degree (S1)", "Bachelor Degree", "S1")
    if "notice" in q or "current employer" in q or "available" in q:
        return pick("1 month") or pick("Less than 1 month")
    if "forklift" in q or "licence" in q or "license" in q or "sio" in q:
        return pick("None of these")
    if "english" in q and "language" in q:
        return pick("Speaks proficiently in a professional setting", "Writes proficiently in a professional setting")
    if "years" in q or "experience" in q or "pengalaman" in q or any(norm == "3 years" for _, norm in usable):
        return pick("3 years", "3 tahun") or pick("More than 5 years")
    return pick("None of these") or usable[0][0]

def choose_salary_option_index(option_texts: list[str], expected_salary: str) -> int | None:
    expected = parse_expected_salary(expected_salary)
    if expected <= 0:
        return None
    salary_options = []
    for idx, raw in enumerate(option_texts):
        if is_placeholder_option(raw):
            continue
        amounts = parse_salary_amounts(raw)
        if amounts:
            salary_options.append((idx, max(amounts)))
    if not salary_options:
        return None
    at_or_above = [(idx, amount) for idx, amount in salary_options if amount >= expected]
    if at_or_above:
        return min(at_or_above, key=lambda item: item[1])[0]
    return min(salary_options, key=lambda item: abs(item[1] - expected))[0]

def dropdown_has_salary_options(option_texts: list[str]) -> bool:
    count = 0
    for raw in option_texts:
        if is_placeholder_option(raw):
            continue
        if parse_salary_amounts(raw):
            count += 1
    return count >= 2

def compact_jobstreet_question_chunk(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip(" -:;\n\t")
    starters = list(re.finditer(
        r"\b(how many|what(?:'s| is)?|which|do you|are you|can you|berapa|apa|kapan|apakah|seberapa)\b",
        cleaned,
        flags=re.I,
    ))
    if starters:
        starter = starters[0] if starters[0].start() <= 2 else starters[-1]
        cleaned = cleaned[starter.start():].strip(" -:;\n\t")
    return cleaned[:350]

def trim_jobstreet_question(text: str, answer_hint: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""
    matches = list(re.finditer(r"[^?]{3,}\?", cleaned))
    if not matches:
        return cleaned[:350]
    hint = (answer_hint or "").strip().lower()
    lowered = cleaned.lower()
    if hint:
        idx = lowered.find(hint)
        if idx >= 0:
            before_hint = [m for m in matches if m.end() <= idx]
            if before_hint:
                return compact_jobstreet_question_chunk(before_hint[-1].group(0))
    hint_norm = normalize_text(answer_hint)
    keyword_groups = []
    if parse_salary_amounts(answer_hint) or any(k in hint_norm for k in ("rp", "idr", "million", "juta", "jt")):
        keyword_groups.append(("salary", "gaji", "expected", "harapkan", "bulanan", "basic"))
    if any(k in hint_norm for k in ("year", "years", "tahun")):
        keyword_groups.append(("experience", "pengalaman", "years", "tahun"))
    if any(k in hint_norm for k in ("english", "bahasa", "speak", "write", "proficient")):
        keyword_groups.append(("language", "bahasa", "english", "speak", "write", "proficient"))
    for keywords in keyword_groups:
        for match in matches:
            question = match.group(0).lower()
            if any(keyword in question for keyword in keywords):
                return compact_jobstreet_question_chunk(match.group(0))
    return compact_jobstreet_question_chunk(matches[0].group(0))

def build_jobstreet_dropdown_prompt(label: str, context: str, option_texts: list[str]) -> str:
    options = [t for t in option_texts if t]
    question = (label or "").strip()
    if not question or question.lower() == "dropdown question":
        stripped = context or ""
        for opt in sorted(options, key=len, reverse=True):
            stripped = stripped.replace(opt, " ")
        stripped = re.sub(r"\b(Silakan buat seleksi|Please make a selection|Dropdown question|Options?)\b", " ", stripped, flags=re.I)
        stripped = re.sub(r"\s+", " ", stripped).strip(" -:;\n\t")
        question = trim_jobstreet_question(stripped, "; ".join(options)) or "Dropdown question"
    return f"{question}\nOptions: " + "; ".join(options)[:500]

async def has_jobstreet_session(page, context) -> bool:
    try:
        cookies = await context.cookies(JOBSTREET_COOKIE_URLS)
        names   = [c.get("name") for c in cookies]
        if any(n in names for n in JOBSTREET_SESSION_COOKIES):
            return True
    except Exception:
        pass
    for sel in JOBSTREET_LOGGED_IN_SELECTORS:
        try:
            if await page.locator(sel).first.is_visible(timeout=800):
                return True
        except Exception:
            pass
    return False


class JobStreetBot:
    def __init__(self, user_id, on_apply, emit, ask_user_question=None, should_stop=None):
        self.user_id  = user_id
        self.on_apply = on_apply
        self.emit     = emit
        self.ask_user_question = ask_user_question
        self.should_stop = should_stop or (lambda: False)
        self._browser = None
        self._target_locations_by_position = {}

    def _progress(self, job_id, title, company, loc, step, status, msg=""):
        self.emit({
            "type": "progress", "platform": "jobstreet",
            "job_id": job_id, "job_title": title, "company": company,
            "location": loc, "step": step, "status": status, "message": msg,
        })

    async def run(self, targets):
        self._target_locations_by_position = {}
        for target in targets:
            key = normalize_text(target.get("position") or "")
            loc = (target.get("location") or "").strip()
            if key and loc:
                self._target_locations_by_position.setdefault(key, [])
                if loc not in self._target_locations_by_position[key]:
                    self._target_locations_by_position[key].append(loc)

        state_path  = storage_state_path(self.user_id, "jobstreet")
        has_session = os.path.exists(state_path)

        async with async_playwright() as p:
            browser = await launch_browser(p, headless=get_headless_mode(self.user_id))
            self._browser = browser
            ctx_opts = {"user_agent": USER_AGENT}
            if has_session:
                ctx_opts["storage_state"] = state_path
            context = await browser.new_context(**ctx_opts)
            page    = await context.new_page()

            self.emit({"type": "status", "platform": "jobstreet", "message": "Memverifikasi session..."})
            try:
                await page.goto(JOBSTREET_HOME, timeout=30000)
            except Exception:
                pass

            if not await has_jobstreet_session(page, context):
                if has_session:
                    self.emit({"type": "cookie_expired", "platform": "jobstreet",
                               "message": "Cookie JobStreet expired. Login ulang dan upload cookie baru via /cookie jobstreet."})
                    self.emit({"type": "error", "platform": "jobstreet",
                               "message": "Session expired. Login ulang via /cookie jobstreet."})
                    await browser.close()
                    return
                for _ in range(90):
                    if self.should_stop():
                        raise asyncio.CancelledError()
                    await asyncio.sleep(2)
                    if await has_jobstreet_session(page, context):
                        await context.storage_state(path=state_path)
                        break
                else:
                    self.emit({"type": "error", "platform": "jobstreet", "message": "Login timeout."})
                    await browser.close()
                    return

            self.emit({"type": "status", "platform": "jobstreet",
                       "message": "Session valid, mulai mencari lowongan..."})

            for target in targets:
                if self.should_stop():
                    raise asyncio.CancelledError()
                try:
                    await self._search_and_apply(context, page, target)
                except Exception as e:
                    self.emit({"type": "error", "platform": "jobstreet",
                               "message": f"Error target {target.get('position')}: {e}"})

            await browser.close()

    async def _search_and_apply(self, context, page, target):
        position       = target["position"]
        location       = target["location"]
        cv_path        = target["file_path"]
        cv_name        = target.get("file_name") or os.path.basename(cv_path)
        cv_text        = target.get("cv_text", "")
        cover_template = target.get("cover_letter") or ""
        employment_type = target.get("employment_type") or "full_time"
        expected_salary = (target.get("expected_salary") or "").strip() or get_expected_salary(self.user_id)
        accepted_locations = self._target_locations_by_position.get(normalize_text(position), [location])

        self.emit({"type": "status", "platform": "jobstreet",
                   "message": f"Mencari: {position} di {location}"})
        print(f"[ORDAL] JobStreetBot search START: {position} @ {location}")

        pos_slug  = position.replace(" ", "-").lower()
        loc_param = location.replace(" ", "+")
        url = (f"https://id.jobstreet.com/id/{pos_slug}-jobs"
               f"?where={loc_param}&sortmode=ListedDate")

        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await self._delay(2, 3)
        except Exception:
            return

        job_counter           = 0
        consecutive_duplicates = 0
        seen_job_keys = set()

        for page_num in range(1, 51):
            if self.should_stop():
                raise asyncio.CancelledError()
            cards = await page.query_selector_all('article[data-job-id]')
            if not cards:
                cards = await page.query_selector_all('[data-automation="normalJob"]')
            if not cards:
                self.emit({"type": "status", "platform": "jobstreet",
                           "message": f"Tidak ada lowongan di halaman {page_num}"})
                break

            self.emit({"type": "status", "platform": "jobstreet",
                       "message": f"Halaman {page_num}: {len(cards)} lowongan ditemukan"})

            for card in cards:
                if self.should_stop():
                    raise asyncio.CancelledError()
                try:
                    card_job_id = await card.get_attribute("data-job-id") or ""
                    card_text = await card.inner_text()
                    summary = await self._extract_card_summary(card, card_text)
                    match_text = summary["title"] or card_text
                    if not matches_position(match_text, position) and not matches_position(card_text, position):
                        self._progress(
                            f"js_skip_{page_num}_{random.randint(1000,9999)}",
                            summary["title"] or "Lowongan JobStreet",
                            summary["company"],
                            summary["location"] or location,
                            "kesesuaian", "skip", "Posisi tidak sesuai",
                        )
                        continue

                    card_key = card_job_id or normalize_text(card_text)[:180]
                    if card_key in seen_job_keys:
                        continue
                    seen_job_keys.add(card_key)

                    job_counter += 1
                    job_id = f"js_{job_counter}_{random.randint(1000,9999)}"

                    await card.click()
                    await self._delay(1.5, 2.5)

                    is_dup = await self._process_job(
                        context, page, cv_path, cv_text,
                        position, location, job_id, cover_template, cv_name, expected_salary, accepted_locations,
                        employment_type,
                    )

                    if is_dup:
                        consecutive_duplicates += 1
                        if consecutive_duplicates >= 10:
                            self.emit({"type": "status", "platform": "jobstreet",
                                       "message": "10 duplikat berturut-turut — halaman ini sudah dicek sebelumnya"})
                            return
                    else:
                        consecutive_duplicates = 0

                except Exception as e:
                    print(f"[ORDAL] JobStreetBot card loop error: {e}")
                    continue

            # Next page — try multiple selectors
            went_next = False
            for next_sel in [
                '[data-automation="page-next"]',
                '[aria-label="Next page"]',
                'a[rel="next"]',
                'button:has-text("Next")',
            ]:
                try:
                    nxt = await page.query_selector(next_sel)
                    if nxt and await nxt.is_visible():
                        await nxt.click()
                        await self._delay(2, 3)
                        went_next = True
                        break
                except Exception:
                    continue
            if not went_next:
                fallback_url = url + f"&page={page_num + 1}"
                try:
                    before_ids = set(seen_job_keys)
                    await page.goto(fallback_url, timeout=60000, wait_until="domcontentloaded")
                    await self._delay(2, 3)
                    probe_cards = await page.query_selector_all('article[data-job-id]')
                    if not probe_cards:
                        probe_cards = await page.query_selector_all('[data-automation="normalJob"]')
                    probe_ids = set()
                    for probe in probe_cards[:8]:
                        pid = await probe.get_attribute("data-job-id") or ""
                        if not pid:
                            pid = normalize_text(await probe.inner_text())[:180]
                        if pid:
                            probe_ids.add(pid)
                    if probe_cards and not probe_ids.issubset(before_ids):
                        went_next = True
                    else:
                        self.emit({"type": "status", "platform": "jobstreet",
                                   "message": f"Tidak ada halaman berikutnya setelah halaman {page_num}"})
                        break
                except Exception as e:
                    self.emit({"type": "status", "platform": "jobstreet",
                               "message": f"Tidak ada halaman berikutnya setelah halaman {page_num}: {str(e)[:50]}"})
                    break

    async def _extract_card_summary(self, card, fallback_text: str = "") -> dict:
        try:
            data = await card.evaluate(
                r"""
                (el) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const pick = sels => {
                        for (const sel of sels) {
                            const node = el.querySelector(sel);
                            const text = clean(node && (node.innerText || node.textContent));
                            if (text) return text;
                        }
                        return '';
                    };
                    const lines = (el.innerText || el.textContent || '').split(/\n|\r/).map(clean).filter(Boolean);
                    const dateLike = t => /^(listed|dipasang|ditayangkan|posted|lebih dari|\d+\s+(hari|day|days|jam|hour|hours))/i.test(t || '');
                    const title = pick([
                        '[data-automation="jobTitle"]',
                        'a[data-automation="jobTitle"]',
                        '[data-automation*="job-title" i]',
                        'a[href*="/job/"]',
                        'h1', 'h2', 'h3'
                    ]) || lines.find(t => !dateLike(t) && !/salary|gaji|rp\s*\d/i.test(t)) || '';
                    const company = pick([
                        '[data-automation="jobCompany"]',
                        '[data-automation="advertiser-name"]',
                        '[data-automation*="company" i]',
                        '[data-automation*="advertiser" i]'
                    ]);
                    const location = pick([
                        '[data-automation="jobLocation"]',
                        '[data-automation*="location" i]'
                    ]);
                    return { title, company, location };
                }
                """
            )
            return {
                "title": data.get("title") or "",
                "company": data.get("company") or "",
                "location": data.get("location") or "",
            }
        except Exception:
            lines = [line.strip() for line in (fallback_text or "").splitlines() if line.strip()]
            title = next((line for line in lines if not re.match(r"^(listed|posted|dipasang|ditayangkan)", line, re.I)), "")
            return {"title": title, "company": lines[1] if len(lines) > 1 else "", "location": ""}

    async def _process_job(self, context, page, cv_path, cv_text,
                           position, location, job_id, cover_template="", cv_name="", expected_salary="", accepted_locations=None, employment_type="full_time") -> bool:
        """Returns True if duplicate."""
        job_title = "Unknown"; company = "Unknown"; job_location = ""; salary = ""
        try:
            job_title = first_nonempty(
                await safe_text(page, '[data-automation="job-detail-title"]'),
                await safe_text(page, 'h1[data-automation*="title"]'),
                "Unknown"
            )
            company = first_nonempty(
                await safe_text(page, '[data-automation="advertiser-name"]'),
                await safe_text(page, '[data-automation="job-detail-company"]'),
                "Unknown"
            )
            company = clean_company_name(company)
            job_location = first_nonempty(
                await safe_text(page, '[data-automation="job-detail-location"]'),
                await safe_text(page, '[data-automation="job-detail-work-location"]'),
                await safe_text(page, '[data-automation="job-detail-header-location"]'),
                await safe_text(page, 'span[data-automation*="location"]'),
            )
            salary  = clean_salary(first_nonempty(
                await safe_text(page, '[data-automation="job-detail-salary"]'),
                await safe_text(page, '[data-automation="job-detail-header-salary"]'),
            ))
            job_url = page.url

            self._progress(job_id, job_title, company, job_location or location, "analisis", "running")
            await self._delay(0.2, 0.4)
            self._progress(job_id, job_title, company, job_location or location, "analisis", "ok")

            # Kesesuaian
            self._progress(job_id, job_title, company, job_location or location, "kesesuaian", "running")
            detail_text = await safe_text(page, '[data-automation="jobAdDetails"]') or ""
            pos_ok = matches_position(job_title, position)
            loc_ok, loc_reason = self._matches_target_location(job_location, location, accepted_locations or [])
            salary_ok, salary_reason = salary_matches(expected_salary, salary)
            employment_ok, employment_reason = matches_employment_type(
                " ".join([job_title, detail_text, salary]), employment_type,
            )

            if not pos_ok or not loc_ok or not salary_ok or not employment_ok:
                reason = "Posisi tidak sesuai" if not pos_ok else ("Lokasi tidak sesuai" if not loc_ok else (salary_reason if not salary_ok else employment_reason))
                self._progress(job_id, job_title, company, job_location or location,
                               "kesesuaian", "skip", reason)
                await self.on_apply("jobstreet", job_title, company, job_url,
                                    position, location, "skipped", reason, job_location, salary)
                return False
            self._progress(job_id, job_title, company, job_location or location,
                           "kesesuaian", "ok", loc_reason or salary_reason)

            # Duplikat
            self._progress(job_id, job_title, company, job_location or location, "duplikat", "running")
            is_dup = await self._check_duplicate(job_url, job_title, company)
            if is_dup:
                self._progress(job_id, job_title, company, job_location or location,
                               "duplikat", "skip", "Sudah pernah dilamar")
                await self.on_apply("jobstreet", job_title, company, job_url,
                                    position, location, "skipped", "Sudah pernah dilamar",
                                    job_location, salary)
                return True  # ← duplicate flag
            self._progress(job_id, job_title, company, job_location or location, "duplikat", "ok")

            # Apply
            self._progress(job_id, job_title, company, job_location or location, "apply", "running")

            apply_btn = None
            for sel in [
                '[data-automation="job-detail-apply"]',
                'a[data-automation="job-detail-apply"]',
                'button[data-automation*="apply"]',
                'a[data-automation*="apply"]',
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        apply_btn = el
                        break
                except Exception:
                    pass

            if not apply_btn:
                print(f"[ORDAL] JobStreetBot NO apply button: {job_title} @ {company}")
                self._progress(job_id, job_title, company, job_location or location,
                               "apply", "fail", "Tombol apply tidak ditemukan")
                await self.on_apply("jobstreet", job_title, company, job_url,
                                    position, location, "skipped", "No apply button",
                                    job_location, salary)
                return False

            apply_page = page
            pages_before = len(context.pages)
            try:
                async with context.expect_page(timeout=6000) as page_info:
                    await apply_btn.click()
                apply_page = await page_info.value
                try:
                    await apply_page.wait_for_load_state("domcontentloaded", timeout=20000)
                except Exception:
                    pass
            except Exception:
                await apply_btn.click()
                await self._delay(2, 3)
                if len(context.pages) > pages_before:
                    apply_page = context.pages[-1]
            if apply_page != page:
                try:
                    await apply_page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await self._delay(1, 2)
            if not is_jobstreet_url(apply_page.url):
                self._progress(job_id, job_title, company, job_location or location,
                               "apply", "skip", "Redirect eksternal, balik ke JobStreet")
                await self.on_apply("jobstreet", job_title, company, job_url,
                                    position, location, "skipped", "Redirect eksternal",
                                    job_location, salary)
                self.emit({"type": "status", "platform": "jobstreet",
                           "message": f"Lamaran eksternal ditutup: {apply_page.url[:120]}"})
                if apply_page != page:
                    try:
                        await apply_page.close()
                    except Exception:
                        pass
                else:
                    try:
                        await page.go_back(timeout=10000, wait_until="domcontentloaded")
                    except Exception:
                        pass
                return False
            ready = await self._ensure_apply_page_ready(apply_page)
            if not ready:
                print(f"[ORDAL] JobStreetBot apply page not ready: {apply_page.url}")

            success = await self._fill_and_submit(
                apply_page, page, cv_path, cv_text,
                job_title, company, position, cover_template, cv_name, expected_salary
            )

            if success:
                self._progress(job_id, job_title, company, job_location or location, "apply", "ok")
                await self.on_apply("jobstreet", job_title, company, job_url,
                                    position, location, "applied", None, job_location, salary)
            else:
                self._progress(job_id, job_title, company, job_location or location,
                               "apply", "fail", "Submit tidak terkonfirmasi")
                await self._record_failure(
                    apply_page, job_title, company, job_url, position, location,
                    "Submit tidak terkonfirmasi", "jobstreet_submit",
                )
                await self.on_apply("jobstreet", job_title, company, job_url,
                                    position, location, "failed", "Submit tidak terkonfirmasi",
                                    job_location, salary)
            if len(context.pages) > pages_before:
                try:
                    await apply_page.close()
                except Exception:
                    pass
            return False

        except PlaywrightTimeout as e:
            self._progress(job_id, job_title, company, job_location or location,
                           "apply", "fail", f"Timeout: {str(e)[:60]}")
            await self._record_failure(
                page, job_title, company, page.url, position, location,
                f"Timeout: {str(e)[:120]}", "process_job_timeout",
            )
            return False
        except Exception as e:
            print(f"[ORDAL] JobStreetBot _process_job error: {e}")
            self._progress(job_id, job_title, company, job_location or location,
                           "apply", "fail", str(e)[:60])
            await self._record_failure(
                page, job_title, company, page.url, position, location,
                str(e)[:200], "process_job_exception",
            )
            return False

    async def _fill_and_submit(self, apply_page, detail_page, cv_path, cv_text,
                               job_title, company, position,
                               cover_template: str = "", cv_name: str = "", expected_salary: str = "") -> bool:  # ← FIXED param
        try:
            for step in range(1, 9):
                if self.should_stop():
                    raise asyncio.CancelledError()
                if not is_jobstreet_url(apply_page.url):
                    return False
                if "/apply" not in (apply_page.url or ""):
                    self.emit({"type": "status", "platform": "jobstreet",
                               "message": f"Keluar halaman lamaran, kembali: {apply_page.url[:100]}"})
                    try:
                        await apply_page.go_back(timeout=10000, wait_until="domcontentloaded")
                    except Exception:
                        return False
                    await self._delay(1, 1.5)
                    continue

                await self._fill_jobstreet_step(apply_page, detail_page, cv_path, cv_text,
                                                job_title, company, position, cover_template, cv_name, expected_salary)
                if await self._submission_confirmed(apply_page, attempts=1):
                    return True

                final_words = ["kirim lamaran", "submit application", "send application"]
                final_btn = await self._find_jobstreet_action_button(apply_page, final_words)
                if final_btn:
                    btn_text = await self._element_text(final_btn)
                    self.emit({"type": "status", "platform": "jobstreet", "message": f"Klik tombol submit: {btn_text or 'kirim lamaran'}"})
                    await self._click_jobstreet_button(apply_page, final_btn, final_words)
                    print(f"[ORDAL] JobStreetBot SUBMIT clicked step={step}")
                    await self._delay(2, 3)
                    if await self._submission_confirmed(apply_page):
                        return True
                    continue

                next_words = [
                    "lanjut", "lanjutkan", "berikutnya", "continue", "next",
                    "save and continue", "simpan dan lanjut",
                ]
                next_btn = await self._find_jobstreet_action_button(apply_page, next_words)
                if not next_btn:
                    next_btn = await self._find_visible_button(apply_page, [
                        'button:has-text("Continue")',
                        'button:has-text("Next")',
                        'button:has-text("Lanjut")',
                        'button:has-text("Lanjutkan")',
                        'button:has-text("Berikutnya")',
                        'button:has-text("Perbarui profil")',
                        'button:has-text("Update profile")',
                        'button:has-text("Review")',
                        'button:has-text("Tinjau")',
                        'button:has-text("Save and continue")',
                        'button:has-text("Simpan dan lanjut")',
                        'a:has-text("Lanjut")',
                        '[role="button"]:has-text("Lanjut")',
                        'button[data-automation*="continue"]',
                        'button[data-automation*="next"]',
                    ])
                if next_btn:
                    if await self._resume_required_visible(apply_page) and not await self._resume_selected(apply_page):
                        await self._record_failure(
                            apply_page, job_title, company, detail_page.url, position, "",
                            "CV belum terpilih sebelum lanjut", "jobstreet_resume_before_next",
                        )
                        return False
                    before_sig = await self._jobstreet_step_marker(apply_page)
                    btn_text = await self._element_text(next_btn)
                    self.emit({"type": "status", "platform": "jobstreet", "message": f"Klik tombol: {btn_text or 'lanjut'}"})
                    await self._click_jobstreet_button(apply_page, next_btn, next_words)
                    print(f"[ORDAL] JobStreetBot NEXT clicked step={step}")
                    await self._delay(0.8, 1.2)
                    after_sig = await self._jobstreet_step_marker(apply_page)
                    if after_sig == before_sig:
                        clicked_alt = await self._click_jobstreet_action_by_text(apply_page, next_words)
                        if clicked_alt:
                            self.emit({"type": "status", "platform": "jobstreet", "message": "Tombol lanjut diklik ulang dengan fallback"})
                            await self._delay(0.9, 1.3)
                    if await self._jobstreet_step_marker(apply_page) == before_sig:
                        self.emit({"type": "status", "platform": "jobstreet", "message": "Tombol lanjut tidak mengubah halaman, cek tombol lain"})
                    continue
                clicked_any = await self._click_jobstreet_action_by_text(apply_page, [*next_words, "kirim lamaran", "submit", "send application"])
                if clicked_any:
                    self.emit({"type": "status", "platform": "jobstreet", "message": "Tombol lanjut diklik via scan teks"})
                    await self._delay(0.9, 1.3)
                    continue
                buttons = await self._visible_button_texts(apply_page)
                self.emit({"type": "status", "platform": "jobstreet", "message": f"Tombol lanjut tidak ditemukan: {buttons[:120]}"})
                break
            return False
        except Exception as e:
            print(f"[ORDAL] JobStreetBot _fill_and_submit error: {e}")
            return False

    async def _ensure_apply_page_ready(self, page):
        for _ in range(3):
            try:
                await page.wait_for_selector("body", timeout=10000)
                text = await safe_text(page, "body")
                if text.strip():
                    return True
                await page.wait_for_load_state("networkidle", timeout=8000)
                text = await safe_text(page, "body")
                if text.strip():
                    return True
            except Exception:
                pass
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await self._delay(1, 1.5)
        return False

    async def _page_step_signature(self, page) -> str:
        try:
            return await page.evaluate(
                r"""
                () => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const buttons = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')]
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 8 && r.height > 8;
                        })
                        .map(el => clean(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || ''))
                        .filter(Boolean)
                        .join('|')
                        .slice(0, 500);
                    const body = clean(document.querySelector('form')?.innerText || document.body.innerText || '').slice(0, 900);
                    return `${location.href}::${buttons}::${body}`;
                }
                """
            )
        except Exception:
            return page.url or ""

    async def _visible_button_texts(self, page) -> str:
        try:
            return await page.evaluate(
                r"""
                () => [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')]
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 8 && r.height > 8;
                    })
                    .map(el => (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim())
                    .filter(Boolean)
                    .slice(-12)
                    .join(' | ')
                """
            )
        except Exception:
            return ""

    async def _element_text(self, element) -> str:
        try:
            return (await element.evaluate(
                r"""
                (el) => (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '')
                    .replace(/\s+/g, ' ')
                    .trim()
                """
            )).strip()
        except Exception:
            return ""

    async def _jobstreet_step_marker(self, page) -> str:
        try:
            return await page.evaluate(
                r"""
                () => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const text = clean(document.body.innerText || '');
                    let step = 'unknown';
                    if (text.includes('pilih dokumen') || text.includes('select documents')) step = 'documents';
                    if (text.includes('jawab pertanyaan perusahaan') || text.includes('employer questions')) step = 'questions';
                    if (text.includes('perbarui profil jobstreet') || text.includes('update jobstreet profile')) step = 'profile';
                    if (text.includes('review dan kirim') || text.includes('review and send')) step = 'review';
                    if (/lamaran (terkirim|berhasil)|application (submitted|sent)/.test(text)) step = 'submitted';
                    const checked = [...document.querySelectorAll('input[type="radio"]:checked,input[type="checkbox"]:checked,[aria-checked="true"],[aria-selected="true"]')].length;
                    const fields = [...document.querySelectorAll('textarea, select, input[type="text"], input[type="number"], input[type="tel"]')]
                        .map(el => `${el.tagName}:${el.value || el.getAttribute('aria-label') || ''}`)
                        .join('|')
                        .slice(0, 300);
                    return `${location.pathname}${location.search}::${step}::${checked}::${fields}`;
                }
                """
            )
        except Exception:
            return page.url or ""

    async def _find_jobstreet_action_button(self, page, words: list[str]):
        try:
            handle = await page.evaluate_handle(
                r"""
                (words) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const wanted = words.map(clean).filter(Boolean);
                    const primaryWords = ['lanjut', 'lanjutkan', 'berikutnya', 'continue', 'next', 'save and continue', 'simpan dan lanjut', 'kirim lamaran', 'submit', 'send application'];
                    const stepOnly = ['pilih dokumen', 'jawab pertanyaan perusahaan', 'perbarui profil jobstreet', 'review dan kirim'];
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const st = getComputedStyle(el);
                        return r.width > 8 && r.height > 8 && st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
                    };
                    const disabled = el => el.disabled || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true';
                    const clickish = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')];
                    const candidates = clickish
                        .map((el, idx) => {
                            const rect = el.getBoundingClientRect();
                            const text = clean(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '');
                            if (!text || disabled(el) || !visible(el)) return null;
                            if (!wanted.some(word => text.includes(word))) return null;
                            const isPrimary = primaryWords.some(word => text.includes(word));
                            const isStepOnly = stepOnly.some(word => text === word || text.includes(word));
                            const area = rect.width * rect.height;
                            let score = 0;
                            if (isPrimary) score += 1000;
                            if (el.tagName === 'BUTTON' || el.tagName === 'INPUT') score += 200;
                            if (el.getAttribute('type') === 'submit') score += 100;
                            if (rect.top > window.innerHeight * 0.45) score += 80;
                            if (area > 30 && area < 25000) score += 40;
                            if (isStepOnly && !isPrimary) score -= 900;
                            if (/^(pilih dokumen|jawab pertanyaan perusahaan|perbarui profil jobstreet|review dan kirim)$/.test(text)) score -= 500;
                            return { el, score, top: rect.top, left: rect.left, idx };
                        })
                        .filter(Boolean)
                        .sort((a, b) => (b.score - a.score) || (b.top - a.top) || (b.left - a.left) || (a.idx - b.idx));
                    return candidates[0]?.el || null;
                }
                """,
                [w.lower() for w in words],
            )
            element = handle.as_element()
            if element:
                return element
        except Exception:
            pass
        return None

    def _matches_target_location(self, job_location: str, current_location: str, accepted_locations: list) -> tuple[bool, str]:
        if not job_location:
            return True, ""
        locations = []
        for loc in [current_location, *(accepted_locations or [])]:
            loc = (loc or "").strip()
            if loc and loc not in locations:
                locations.append(loc)
        for loc in locations:
            if matches_location(job_location, loc):
                if normalize_text(loc) != normalize_text(current_location):
                    return True, f"Lokasi cocok dengan target {loc}"
                return True, ""
        return False, ""

    async def _prepare_jobstreet_document_step(self, page, cv_name: str, cover_text: str) -> bool:
        try:
            result = await page.evaluate(
                r"""
                async ({ cvName, coverText }) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const lower = s => clean(s).toLowerCase();
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const st = getComputedStyle(el);
                        return r.width > 8 && r.height > 8 && st.display !== 'none' && st.visibility !== 'hidden';
                    };
                    const click = el => {
                        if (!el) return false;
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        el.focus?.();
                        el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerType: 'mouse' }));
                        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerType: 'mouse' }));
                        el.click();
                        return true;
                    };
                    const clickOption = patterns => {
                        const nodes = [...document.querySelectorAll('label, button, [role="button"], [role="radio"], div, span')]
                            .filter(visible)
                            .map(el => ({ el, text: lower(el.innerText || el.textContent || el.getAttribute('aria-label') || '') }))
                            .filter(item => patterns.some(p => item.text.includes(p)))
                            .sort((a, b) => {
                                const ar = a.el.getBoundingClientRect();
                                const br = b.el.getBoundingClientRect();
                                return (ar.width * ar.height) - (br.width * br.height);
                            });
                        for (const item of nodes) {
                            const target = item.el.closest('label, button, [role="button"], [role="radio"]') || item.el;
                            const input = target.querySelector?.('input[type="radio"], input[type="checkbox"]');
                            if (input?.checked || target.getAttribute('aria-checked') === 'true') return true;
                            if (click(target)) return true;
                        }
                        return false;
                    };
                    const selectedResume = clickOption(['resume terlampir', 'attached resume', 'pilih resume', 'select resume']);
                    const selectedCover = coverText ? clickOption(['tulis surat lamaran', 'write cover letter', 'cover letter']) : clickOption(['jangan sertakan surat lamaran', 'do not include cover letter']);
                    let filledCover = false;
                    if (coverText) {
                        await new Promise(resolve => setTimeout(resolve, 150));
                        const areas = [...document.querySelectorAll('textarea')].filter(visible);
                        const area = areas.sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height) - (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
                        if (area && clean(area.value) !== clean(coverText)) {
                            area.focus();
                            area.value = coverText;
                            area.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: coverText }));
                            area.dispatchEvent(new Event('change', { bubbles: true }));
                            area.blur();
                            filledCover = true;
                        }
                    }
                    return { selectedResume, selectedCover, filledCover };
                }
                """,
                {"cvName": cv_name or "", "coverText": cover_text or ""},
            )
            if isinstance(result, dict) and (result.get("selectedResume") or result.get("selectedCover") or result.get("filledCover")):
                self.emit({"type": "status", "platform": "jobstreet", "message": "Dokumen lamaran diverifikasi"})
                await self._delay(0.2, 0.4)
                return True
        except Exception:
            pass
        return False

    async def _fill_jobstreet_step(self, apply_page, detail_page, cv_path, cv_text,
                                   job_title, company, position, cover_template: str, cv_name: str = "", expected_salary: str = ""):
        if "/review" in (apply_page.url or ""):
            return

        cover_company = company_for_cover(company)
        cover_text = render_cover_letter_template(cover_template, job_title, cover_company)
        if not cover_text:
            try:
                jd_el   = await detail_page.query_selector('[data-automation="jobAdDetails"]')
                jd_text = await jd_el.inner_text() if jd_el else ""
            except Exception:
                jd_text = ""
            cover_text = await generate_cover_letter(job_title, cover_company, jd_text, cv_text)

        await self._prepare_jobstreet_document_step(apply_page, cv_name or os.path.basename(cv_path), cover_text)

        resume_selected = await self._select_resume_dropdown(apply_page, cv_name or os.path.basename(cv_path))
        if resume_selected or await self._select_existing_resume(apply_page, cv_name or os.path.basename(cv_path)):
            resume_selected = True
            self.emit({"type": "status", "platform": "jobstreet",
                       "message": f"CV dipilih: {cv_name or os.path.basename(cv_path)}"})
        try:
            for fi in await apply_page.query_selector_all('input[type="file"]'):
                try:
                    if resume_selected or await self._select_resume_dropdown(apply_page, cv_name or os.path.basename(cv_path)) or await self._select_existing_resume(apply_page, cv_name or os.path.basename(cv_path)):
                        resume_selected = True
                        continue
                    await fi.set_input_files(prepare_upload_file(cv_path, cv_name))
                    resume_selected = True
                    self.emit({"type": "status", "platform": "jobstreet",
                               "message": f"CV diunggah: {cv_name or os.path.basename(cv_path)}"})
                    await self._delay(0.6, 1)
                except Exception:
                    continue
        except Exception:
            pass

        try:
            for textarea in await apply_page.query_selector_all('textarea'):
                try:
                    label = await self._field_label(apply_page, textarea)
                    if is_resume_field(label):
                        continue
                    if cover_text:
                        try:
                            current = await textarea.input_value()
                        except Exception:
                            current = ""
                        if (current or "").strip() == cover_text.strip():
                            continue
                        await textarea.fill(cover_text)
                        try:
                            await textarea.dispatch_event("input")
                            await textarea.dispatch_event("change")
                            await textarea.evaluate("el => el.blur()")
                        except Exception:
                            pass
                        self.emit({"type": "status", "platform": "jobstreet",
                                   "message": f"Cover letter diganti: {cover_company}"})
                        await self._delay(0.3, 0.6)
                except Exception:
                    continue
        except Exception:
            pass

        try:
            for field in await apply_page.query_selector_all(
                'input[type="text"], input[type="number"], input[type="tel"], input[type="email"], input:not([type])'
            ):
                try:
                    if await field.input_value():
                        continue
                    label = await self._field_label(apply_page, field)
                    if not label:
                        continue
                    if is_resume_field(label):
                        continue
                    ft  = await field.get_attribute("type") or "text"
                    ans = await answer_application_question(self.user_id, "jobstreet", label, ft, cv_text, position, self.ask_user_question)
                    if ans:
                        await field.fill(ans)
                        await self._delay(0.1, 0.3)
                except Exception:
                    continue
        except Exception:
            pass

        await self._fill_jobstreet_choice_questions(apply_page, cv_text, job_title, expected_salary)

        try:
            for sel in await apply_page.query_selector_all("select"):
                try:
                    value = await sel.input_value()
                    selected_text = await sel.evaluate(
                        "el => el.options && el.selectedIndex >= 0 ? (el.options[el.selectedIndex].text || '') : ''"
                    )
                    label = await self._field_label(apply_page, sel)
                    if is_resume_field(label):
                        await self._select_resume_dropdown(apply_page, cv_name or os.path.basename(cv_path))
                        continue
                    opts = await sel.query_selector_all("option")
                    option_texts = await sel.evaluate(
                        "el => Array.from(el.options || []).map(o => (o.textContent || o.label || o.value || '').trim())"
                    )
                    context = await sel.evaluate(
                        "el => (el.closest('fieldset, [role=\"group\"], section, form, div')?.innerText || '').replace(/\\s+/g, ' ').trim()"
                    )
                    prompt = build_jobstreet_dropdown_prompt(label, context, option_texts)
                    expected_salary_pref = (expected_salary or "").strip() or get_expected_salary(self.user_id)
                    has_salary_options = dropdown_has_salary_options(option_texts)
                    prompt_norm = normalize_text(prompt)
                    if any(k in prompt_norm for k in ("experience", "pengalaman", "work experience", "years", "tahun")):
                        ans = await answer_application_question(self.user_id, "jobstreet", prompt, "dropdown", cv_text, job_title, self.ask_user_question)
                    else:
                        ans = choose_jobstreet_option(prompt, option_texts, expected_salary_pref)
                    if not ans:
                        ans = await answer_application_question(self.user_id, "jobstreet", prompt, "dropdown", cv_text, position, self.ask_user_question)
                    ans_norm = normalize_text(ans)
                    selected_norm = normalize_text(selected_text)
                    if value and not is_placeholder_option(selected_text):
                        if not ans_norm or selected_norm == ans_norm or ans_norm in selected_norm or selected_norm in ans_norm:
                            save_question_answer(self.user_id, "jobstreet", prompt, selected_text, "dropdown", source="observed")
                            continue
                    salary_index = choose_salary_option_index(option_texts, expected_salary_pref)
                    if has_salary_options and salary_index is not None:
                        txt_raw = option_texts[salary_index]
                        current_amounts = parse_salary_amounts(selected_text)
                        target_amounts = parse_salary_amounts(txt_raw)
                        if current_amounts and target_amounts and max(current_amounts) == max(target_amounts):
                            continue
                        await sel.select_option(index=salary_index)
                        save_question_answer(self.user_id, "jobstreet", prompt, txt_raw, "dropdown", source="preference")
                        self.emit({"type": "status", "platform": "jobstreet", "message": f"Dropdown gaji dipilih: {txt_raw}"})
                        await self._delay(0.15, 0.3)
                        raise StopIteration
                    if has_salary_options:
                        self.emit({"type": "status", "platform": "jobstreet", "message": "Dropdown gaji dilewati: opsi nominal tidak bisa dibaca"})
                        continue
                    if ans_norm:
                        for i, txt_raw in enumerate(option_texts):
                            txt = normalize_text(txt_raw)
                            if txt and (ans_norm in txt or txt in ans_norm):
                                await sel.select_option(index=i)
                                save_question_answer(self.user_id, "jobstreet", prompt, txt_raw, "dropdown", source="observed")
                                self.emit({"type": "status", "platform": "jobstreet", "message": f"Dropdown dipilih: {txt_raw}"})
                                await self._delay(0.15, 0.3)
                                raise StopIteration
                    for i, opt in enumerate(opts):
                        raw_text = option_texts[i] if i < len(option_texts) else await opt.get_attribute("value") or ""
                        txt = normalize_text(raw_text)
                        if i > 0 and txt and not is_placeholder_option(txt):
                            await sel.select_option(index=i)
                            save_question_answer(self.user_id, "jobstreet", prompt, raw_text, "dropdown", source="observed")
                            self.emit({"type": "status", "platform": "jobstreet", "message": f"Dropdown dipilih: {raw_text}"})
                            await self._delay(0.15, 0.3)
                            break
                except StopIteration:
                    continue
                except Exception:
                    continue
        except Exception:
            pass

    async def _fill_jobstreet_choice_questions(self, page, cv_text: str = "", job_title: str = "", expected_salary: str = ""):
        try:
            experience_answer = await answer_application_question(
                self.user_id,
                "jobstreet",
                "How many years of purchasing, procurement, or supply chain experience do you have?\nOptions: 3 years; 4 years; 5 years; More than 5 years",
                "dropdown",
                cv_text,
                job_title,
                self.ask_user_question,
            )
            clicked = await page.evaluate(
                r"""
                async ({ expectedSalaryRaw, experienceAnswerRaw }) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const lower = s => clean(s).toLowerCase();
                    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
                    const expectedSalary = Number(String(expectedSalaryRaw || '').replace(/\D/g, '')) || 0;
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return r.width > 8 && r.height > 8 &&
                            style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                    };
                    const selected = el => {
                        if (!el) return false;
                        const input = el.matches('input') ? el : el.querySelector('input[type="radio"], input[type="checkbox"]');
                        if (input && input.checked) return true;
                        if (el.matches && el.matches('label') && el.htmlFor) {
                            const linked = document.getElementById(el.htmlFor);
                            if (linked && linked.checked) return true;
                        }
                        const target = el.closest('label, button, [role="radio"], [role="checkbox"], [role="option"], [role="button"]') || el;
                        if (target.matches && target.matches('label') && target.htmlFor) {
                            const linked = document.getElementById(target.htmlFor);
                            if (linked && linked.checked) return true;
                        }
                        if (target.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"], [aria-selected="true"]')) return true;
                        const aria = (target.getAttribute('aria-checked') || target.getAttribute('aria-selected') || '').toLowerCase();
                        if (aria === 'true') return true;
                        return /selected|checked|active/i.test(target.className || '');
                    };
                    const clickableFor = el => {
                        if (!el) return null;
                        if (el.matches('input[type="radio"], input[type="checkbox"]')) return el;
                        const label = el.closest('label');
                        if (label) return label;
                        const role = el.closest('[role="radio"], [role="checkbox"], [role="option"], [role="button"], button');
                        if (role) return role;
                        const ownedInput = el.querySelector && el.querySelector('input[type="radio"], input[type="checkbox"]');
                        if (ownedInput) return ownedInput;
                        let cur = el;
                        for (let i = 0; i < 5 && cur; i += 1, cur = cur.parentElement) {
                            const input = cur.querySelector && cur.querySelector('input[type="radio"], input[type="checkbox"]');
                            if (input && visible(cur)) return input.closest('label') || input;
                        }
                        return el;
                    };
                    const groupFor = el => {
                        const target = clickableFor(el) || el;
                        const input = target.matches('input') ? target : target.querySelector('input[type="radio"], input[type="checkbox"]');
                        if (input?.name) {
                            const byName = [...document.querySelectorAll(`input[name="${CSS.escape(input.name)}"]`)];
                            if (byName.length) return { input, inputs: byName, root: input.closest('fieldset, [role="radiogroup"], [role="group"], section, form') || input.parentElement };
                        }
                        const root = target.closest('fieldset, [role="radiogroup"], [role="group"], section, form') || target.parentElement;
                        const inputs = root ? [...root.querySelectorAll('input[type="radio"], input[type="checkbox"]')] : [];
                        return { input, inputs, root };
                    };
                    const textNear = el => lower(el?.closest('label, [role="radio"], [role="checkbox"], [role="option"], li, div')?.innerText || el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || el?.value || '');
                    const selectedTexts = group => {
                        const out = [];
                        for (const input of group.inputs || []) {
                            if (!input.checked && input.getAttribute('aria-checked') !== 'true') continue;
                            const label = input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null;
                            out.push(textNear(label || input));
                        }
                        if (group.root) {
                            for (const el of group.root.querySelectorAll('[aria-checked="true"], [aria-selected="true"], .selected, .checked, .active')) {
                                out.push(textNear(el));
                            }
                        }
                        return out.filter(Boolean);
                    };
                    const trimQuestionFromRaw = (rawText, answerText = '') => {
                        const text = clean(rawText);
                        if (!text) return '';
                        const compactQuestionChunk = value => {
                            let out = clean(value).replace(/^[-:;\s]+|[-:;\s]+$/g, '');
                            const starts = [...out.matchAll(/\b(how many|what(?:'s| is)?|which|do you|are you|can you|berapa|apa|kapan|apakah|seberapa)\b/ig)];
                            if (starts.length) {
                                const firstIndex = starts[0].index || 0;
                                const start = firstIndex <= 2 ? starts[0] : starts[starts.length - 1];
                                out = clean(out.slice(start.index || 0));
                            }
                            return out.slice(0, 350);
                        };
                        const matches = [...text.matchAll(/[^?]{3,}\?/g)].map(match => ({
                            text: compactQuestionChunk(match[0]),
                            start: match.index || 0,
                            end: (match.index || 0) + match[0].length,
                        }));
                        if (!matches.length) return text;
                        const answer = lower(answerText);
                        const loweredText = lower(text);
                        if (answer) {
                            const idx = loweredText.indexOf(answer);
                            if (idx >= 0) {
                                const before = matches.filter(item => item.end <= idx);
                                if (before.length) return before[before.length - 1].text;
                            }
                        }
                        const groups = [];
                        if (parseAmount(answer) || /\b(rp|idr|million|juta|jt)\b/.test(answer)) {
                            groups.push(['salary', 'gaji', 'expected', 'harapkan', 'bulanan', 'basic']);
                        }
                        if (/\b(year|years|tahun)\b/.test(answer)) {
                            groups.push(['experience', 'pengalaman', 'years', 'tahun']);
                        }
                        if (/\b(english|bahasa|speak|write|proficient)\b/.test(answer)) {
                            groups.push(['language', 'bahasa', 'english', 'speak', 'write', 'proficient']);
                        }
                        if (/\b(degree|s1|bachelor)\b/.test(answer)) {
                            groups.push(['education', 'qualification', 'degree', 'pendidikan']);
                        }
                        for (const keywords of groups) {
                            const found = matches.find(item => keywords.some(keyword => lower(item.text).includes(keyword)));
                            if (found) return found.text;
                        }
                        return matches[0].text;
                    };
                    const optionTextsFor = group => {
                        const out = [];
                        for (const input of group.inputs || []) {
                            const label = input.id ? document.querySelector(`label[for="${CSS.escape(input.id)}"]`) : null;
                            out.push(textNear(label || input));
                        }
                        return out.filter(Boolean);
                    };
                    const questionText = (group, answerText = '') => {
                        const root = group?.root;
                        const raw = clean(root?.innerText || '');
                        if (!raw) return 'JobStreet choice question';
                        const scoped = trimQuestionFromRaw(raw, answerText || selectedTexts(group).join(' '));
                        if (scoped && scoped !== raw) return scoped.slice(0, 350);
                        let text = raw;
                        const removals = [...selectedTexts(group), ...optionTextsFor(group)];
                        for (const value of removals.sort((a, b) => b.length - a.length)) {
                            if (value) text = text.replace(new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig'), ' ');
                        }
                        text = text.replace(/\b(3 years|4 years|5 years|more than 5 years|3 tahun|4 tahun|5 tahun|lebih dari 5 tahun|yes|no|none of these|english|bahasa indonesia)\b/ig, ' ');
                        text = trimQuestionFromRaw(text, answerText) || text;
                        text = clean(text).slice(0, 350);
                        return text || raw.slice(0, 350);
                    };
                    const groupAlreadyHas = (el, wanted) => selectedTexts(groupFor(el)).some(text => text === wanted || text.includes(wanted) || wanted.includes(text));
                    const clickEl = async el => {
                        const target = clickableFor(el);
                        if (!target || selected(target)) return false;
                        target.scrollIntoView({ block: 'center', inline: 'center' });
                        await sleep(80);
                        target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        target.click();
                        target.dispatchEvent(new Event('change', { bubbles: true }));
                        await sleep(140);
                        return selected(target) || true;
                    };
                    const optionText = el => lower(el.innerText || el.textContent || el.getAttribute('aria-label') || el.value || '');
                    const parseAmount = text => {
                        const raw = lower(text).replace(/,/g, '.');
                        const match = raw.match(/\d+(?:\.\d+)?/);
                        if (!match) return 0;
                        let n = Number(match[0]);
                        if (!Number.isFinite(n)) return 0;
                        if (/\b(million|juta|jt)\b/.test(raw) || n < 1000) n *= 1000000;
                        return Math.round(n);
                    };
                    const deepestExact = wanted => [...document.querySelectorAll('label, button, [role="radio"], [role="checkbox"], [role="option"], [role="button"], li, span, div, input[type="radio"], input[type="checkbox"]')]
                        .filter(visible)
                        .map(el => ({ el, text: optionText(el) }))
                        .filter(item => item.text === wanted)
                        .filter(item => ![...item.el.children].some(child => visible(child) && optionText(child) === wanted))
                        .sort((a, b) => {
                            const ar = a.el.getBoundingClientRect();
                            const br = b.el.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        });
                    const deepestSalaryOptions = () => [...document.querySelectorAll('label, button, [role="radio"], [role="checkbox"], [role="option"], [role="button"], li, span, div, input[type="radio"], input[type="checkbox"]')]
                        .filter(visible)
                        .map(el => ({ el, text: optionText(el), amount: parseAmount(optionText(el)) }))
                        .filter(item => item.amount > 0 && /\b(rp|idr|million|juta|jt)\b/.test(item.text))
                        .filter(item => ![...item.el.children].some(child => visible(child) && parseAmount(optionText(child)) === item.amount))
                        .sort((a, b) => {
                            const ar = a.el.getBoundingClientRect();
                            const br = b.el.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        });
                    let changed = 0;
                    let verified = 0;
                    const answers = [];
                    const clickedTargets = new Set();
                    if (expectedSalary) {
                        const salaryOptions = deepestSalaryOptions();
                        if (salaryOptions.length) {
                            const atOrAbove = salaryOptions.filter(item => item.amount >= expectedSalary);
                            const chosen = (atOrAbove.length ? atOrAbove : salaryOptions)
                                .sort((a, b) => (atOrAbove.length ? a.amount - b.amount : Math.abs(a.amount - expectedSalary) - Math.abs(b.amount - expectedSalary)))[0];
                            const target = clickableFor(chosen.el);
                            const group = groupFor(chosen.el);
                            const currentAmounts = selectedTexts(group).map(parseAmount).filter(Boolean);
                            if (currentAmounts.includes(chosen.amount) || selected(target)) verified += 1;
                            else if (await clickEl(chosen.el)) changed += 1;
                            answers.push({ question: questionText(group, chosen.text), answer: chosen.text, field_type: 'choice', source: 'preference' });
                        }
                    }
                    const exactOptions = [];
                    const exp = clean(experienceAnswerRaw || '');
                    if (exp) exactOptions.push(exp);
                    exactOptions.push(
                        'Bachelor Degree (S1)',
                        '1 month',
                        'Speaks proficiently in a professional setting',
                        'Writes proficiently in a professional setting',
                        'English',
                        'Bahasa Indonesia',
                        'Yes',
                        'None of these',
                    );
                    for (const option of exactOptions) {
                        const wanted = lower(option);
                        const nodes = deepestExact(wanted);
                        for (const item of nodes) {
                            const target = clickableFor(item.el);
                            if (!target) continue;
                            const key = target.outerHTML ? target.outerHTML.slice(0, 180) : `${wanted}:${changed}:${verified}`;
                            if (clickedTargets.has(key)) continue;
                            clickedTargets.add(key);
                            if (groupAlreadyHas(item.el, wanted) || selected(target)) {
                                verified += 1;
                                answers.push({ question: questionText(groupFor(item.el), item.text), answer: item.text, field_type: 'choice', source: option === exp ? 'cv' : 'observed' });
                                continue;
                            }
                            if (await clickEl(item.el)) {
                                changed += 1;
                                answers.push({ question: questionText(groupFor(item.el), item.text), answer: item.text, field_type: 'choice', source: option === exp ? 'cv' : 'observed' });
                            }
                        }
                    }
                    return { changed, verified, answers };
                }
                """,
                {"expectedSalaryRaw": (expected_salary or "").strip() or get_expected_salary(self.user_id), "experienceAnswerRaw": experience_answer},
            )
            changed = int((clicked or {}).get("changed") or 0) if isinstance(clicked, dict) else int(clicked or 0)
            verified = int((clicked or {}).get("verified") or 0) if isinstance(clicked, dict) else 0
            if isinstance(clicked, dict):
                for item in clicked.get("answers") or []:
                    question = (item.get("question") or "").strip()
                    answer = (item.get("answer") or "").strip()
                    if question and answer:
                        save_question_answer(self.user_id, "jobstreet", question, answer, item.get("field_type") or "choice", source=item.get("source") or "observed")
            if changed or verified:
                detail = []
                if changed:
                    detail.append(f"dipilih {changed}")
                if verified:
                    detail.append(f"sudah benar {verified}")
                self.emit({"type": "status", "platform": "jobstreet", "message": f"Opsi pertanyaan diverifikasi: {', '.join(detail)}"})
                await self._delay(0.25, 0.45)
        except Exception:
            pass

    async def _select_existing_resume(self, page, cv_name: str) -> bool:
        wanted = safe_filename(cv_name)
        if not wanted:
            return False
        try:
            clicked = await page.evaluate(
                r"""
                (wanted) => {
                    const clean = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const needle = clean(wanted);
                    const relaxed = needle.replace(/[_-]+/g, ' ');
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 8 && r.height > 8;
                    };
                    const textOf = el => clean(el.innerText || el.textContent || '');
                    const nodes = [...document.querySelectorAll('label, button, div, span, li, section')]
                        .filter(visible)
                        .filter(el => {
                            const t = textOf(el);
                            return t.includes('.pdf') && (t.includes(needle) || t.replace(/[_-]+/g, ' ').includes(relaxed));
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        });
                    for (const node of nodes) {
                        const row = node.closest('label, [role="radio"], [role="option"], [data-automation], li, section, div') || node;
                        const input = row.querySelector('input[type="radio"], input[type="checkbox"]');
                        if (input) { input.click(); return true; }
                        const radio = row.querySelector('[role="radio"], [aria-checked]');
                        if (radio) { radio.click(); return true; }
                        const button = row.closest('button') || row.querySelector('button, [role="button"]');
                        if (button) { button.click(); return true; }
                        const label = row.closest('label') || row.querySelector('label');
                        if (label) { label.click(); return true; }
                        const r = row.getBoundingClientRect();
                        const targets = [
                            document.elementFromPoint(r.right - 24, r.top + r.height / 2),
                            document.elementFromPoint(r.left + 24, r.top + r.height / 2),
                            row,
                        ].filter(Boolean);
                        for (const target of targets) {
                            target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                            target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                            target.click();
                            if (row.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) return true;
                        }
                    }
                    return false;
                }
                """,
                wanted,
            )
            if clicked:
                await self._delay(0.3, 0.6)
            return bool(clicked and await self._resume_selected(page))
        except Exception:
            return False

    async def _select_resume_dropdown(self, page, cv_name: str) -> bool:
        wanted = safe_filename(cv_name)
        if not wanted:
            return False
        try:
            selected = await page.evaluate(
                r"""
                (wanted) => {
                    const clean = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const normalizeName = s => clean(s).replace(/[_-]+/g, ' ');
                    const needle = normalizeName(wanted);
                    const isResumeText = s => {
                        const t = clean(s);
                        return t.includes('resume') || t.includes('cv') || t.includes('curriculum vitae') || t.includes('riwayat hidup');
                    };
                    const labelFor = el => {
                        const id = el.getAttribute('id');
                        const direct = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
                        const labelledby = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
                            .map(id => id && document.getElementById(id)?.innerText).filter(Boolean).join(' ');
                        const wrapper = el.closest('label, [data-automation], div, section');
                        return [direct?.innerText, labelledby, el.getAttribute('aria-label'), el.getAttribute('placeholder'), wrapper?.innerText].filter(Boolean).join(' ');
                    };
                    const selects = [...document.querySelectorAll('select')].filter(sel => isResumeText(labelFor(sel)));
                    for (const sel of selects) {
                        const current = sel.options[sel.selectedIndex]?.text || '';
                        if (normalizeName(current).includes(needle) || needle.includes(normalizeName(current))) return true;
                        for (const opt of [...sel.options]) {
                            const text = normalizeName(opt.text || opt.label || opt.value || '');
                            if (!text || text.includes('pilih') || text.includes('select')) continue;
                            if (text.includes(needle) || needle.includes(text)) {
                                sel.value = opt.value;
                                sel.dispatchEvent(new Event('input', { bubbles: true }));
                                sel.dispatchEvent(new Event('change', { bubbles: true }));
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                wanted,
            )
            if selected:
                await self._delay(0.3, 0.6)
            return bool(selected)
        except Exception:
            return False

    async def _resume_required_visible(self, page) -> bool:
        try:
            text = normalize_text(await safe_text(page, "body"))
            has_resume = any(word in text for word in ("resume", "cv", "riwayat hidup", "dokumen"))
            has_required = any(word in text for word in (
                "required", "wajib", "harus", "pilih cv", "pilih resume",
                "select resume", "select cv", "unggah cv", "upload resume",
            ))
            return has_resume and has_required
        except Exception:
            return False

    async def _resume_selected(self, page) -> bool:
        try:
            return bool(await page.evaluate(
                r"""
                () => {
                    const clean = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const text = clean(document.body.innerText || '');
                    if (!text.includes('resume') && !text.includes('cv')) return true;
                    if (document.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) return true;
                    const required = ['required', 'wajib', 'harus', 'pilih cv', 'pilih resume', 'select resume', 'select cv']
                        .some(part => text.includes(part));
                    return !required;
                }
                """
            ))
        except Exception:
            return False

    async def _field_label(self, page, field) -> str:
        label_id = await field.get_attribute("aria-labelledby") or ""
        label_el = await page.query_selector(f"#{label_id}") if label_id else None
        label    = await label_el.inner_text() if label_el else ""
        if label:
            return label.strip()
        return (await field.get_attribute("placeholder") or
                await field.get_attribute("aria-label") or "").strip()

    async def _find_visible_button(self, page, selectors):
        for _ in range(4):
            for sel in selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        disabled = await btn.get_attribute("disabled")
                        aria_disabled = await btn.get_attribute("aria-disabled")
                        if disabled is None and aria_disabled != "true":
                            return btn
                except Exception:
                    continue
            try:
                await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
            except Exception:
                pass
            await self._delay(0.2, 0.35)
        try:
            handle = await page.evaluate_handle(
                r"""
                () => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const wanted = ['lanjut', 'lanjutkan', 'berikutnya', 'continue', 'next', 'perbarui profil', 'update profile', 'review', 'tinjau', 'save and continue', 'simpan dan lanjut', 'kirim lamaran', 'submit'];
                    const nodes = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')];
                    return nodes.find(el => {
                        const r = el.getBoundingClientRect();
                        const text = clean(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '');
                        const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                        return !disabled && r.width > 8 && r.height > 8 && wanted.some(word => text.includes(word));
                    }) || null;
                }
                """
            )
            element = handle.as_element()
            if element:
                return element
        except Exception:
            pass
        return None

    async def _click_jobstreet_action_by_text(self, page, words: list[str]) -> bool:
        try:
            return bool(await page.evaluate(
                r"""
                (words) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const primaryWords = ['lanjut', 'lanjutkan', 'berikutnya', 'continue', 'next', 'save and continue', 'simpan dan lanjut', 'kirim lamaran', 'submit', 'send application'];
                    const stepOnly = ['pilih dokumen', 'jawab pertanyaan perusahaan', 'perbarui profil jobstreet', 'review dan kirim'];
                    const candidates = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')]
                        .map((el, idx) => {
                            const rect = el.getBoundingClientRect();
                            const text = clean(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '');
                            const isPrimary = primaryWords.some(word => text.includes(word));
                            const isStepOnly = stepOnly.some(word => text === word || text.includes(word));
                            let score = 0;
                            if (isPrimary) score += 1000;
                            if (el.tagName === 'BUTTON' || el.tagName === 'INPUT') score += 200;
                            if (rect.top > window.innerHeight * 0.45) score += 80;
                            if (isStepOnly && !isPrimary) score -= 900;
                            return { el, rect, text, score, idx };
                        })
                        .filter(item => {
                            const disabled = item.el.disabled || item.el.getAttribute('disabled') !== null || item.el.getAttribute('aria-disabled') === 'true';
                            return !disabled && item.rect.width > 8 && item.rect.height > 8 && words.some(word => item.text.includes(word));
                        })
                        .sort((a, b) => (b.score - a.score) || (b.rect.top - a.rect.top) || (b.rect.left - a.rect.left) || (a.idx - b.idx));
                    const item = candidates[0];
                    if (!item) return false;
                    item.el.scrollIntoView({ block: 'center', inline: 'center' });
                    item.el.focus?.();
                    item.el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerType: 'mouse' }));
                    item.el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    item.el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    item.el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerType: 'mouse' }));
                    item.el.click();
                    return true;
                }
                """,
                [w.lower() for w in words],
            ))
        except Exception:
            return False

    async def _click_jobstreet_button(self, page, button, words: list[str]):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self._delay(0.15, 0.25)
        except Exception:
            pass
        try:
            await button.scroll_into_view_if_needed()
            await button.click(force=True, timeout=8000)
            return
        except Exception:
            pass
        try:
            if await self._click_jobstreet_action_by_text(page, words):
                return
        except Exception:
            pass
        await button.scroll_into_view_if_needed()
        await button.press("Enter", timeout=3000)

    async def _submission_confirmed(self, page, attempts: int = 8) -> bool:
        success_phrases = [
            "application submitted", "application sent", "successfully applied",
            "your application has been submitted", "lamaran terkirim", "lamaran berhasil",
            "berhasil dikirim", "applied successfully", "application received",
        ]
        failure_phrases = ["required", "wajib", "must be completed", "please complete", "error"]
        for _ in range(max(1, attempts)):
            try:
                url = (page.url or "").lower()
                if any(part in url for part in ("application-submitted", "applied", "success")):
                    return True
                body = normalize_text(await safe_text(page, "body"))
                if any(phrase in body for phrase in success_phrases):
                    return True
                if any(phrase in body for phrase in failure_phrases):
                    return False
            except Exception:
                pass
            await self._delay(0.5, 0.8)
        return False

    async def _record_failure(self, page, job_title, company, job_url, position, location, reason, step):
        buttons = []
        try:
            for btn in (await page.query_selector_all("button"))[:30]:
                try:
                    text = normalize_text(await btn.inner_text())
                    if text:
                        buttons.append(text[:120])
                except Exception:
                    continue
        except Exception:
            pass
        body_text = ""
        page_content = ""
        try:
            body_text = await safe_text(page, "body")
        except Exception:
            pass
        try:
            page_content = (await page.content())[:3000]
        except Exception:
            pass
        try:
            log_failure({
                "platform": "jobstreet",
                "step": step,
                "reason": reason,
                "job_title": job_title,
                "company": company,
                "job_url": job_url,
                "position": position,
                "location": location,
                "page_url": page.url,
                "visible_buttons": buttons,
                "body_text": body_text,
                "page_content": page_content,
            })
        except Exception:
            pass

    async def _check_duplicate(self, job_url: str, job_title: str, company: str) -> bool:
        try:
            from database import get_db
            db  = get_db()
            cur = db.execute("""
                SELECT COUNT(*) FROM apply_logs
                WHERE (job_url = ? OR (job_title = ? AND company = ?))
                AND status = 'applied'
                AND confirmed_at IS NOT NULL
            """, (job_url, job_title, company))
            count = cur.fetchone()[0]
            db.close()
            return count > 0
        except Exception:
            return False

    async def _delay(self, min_s=1.0, max_s=3.0):
        if self.should_stop():
            try:
                if self._browser:
                    await self._browser.close()
            except Exception:
                pass
            raise asyncio.CancelledError()
        await asyncio.sleep(random.uniform(min_s, max_s))
