"""
LinkedIn Easy Apply Bot v3 — all bugs fixed
Fixes:
1. Infinite scroll (not pagination — LinkedIn has no Next button)
2. matches_position: ANY word (was ALL)
3. cover_template properly scoped in _complete_easy_apply
4. Updated selectors (LinkedIn changes HTML frequently)
5. Duplicate stop at 10 consecutive
6. 2025+ LinkedIn HTML selectors (base-search-card classes)
7. location aliases for region matching
"""
import asyncio
import os
import random
import re
from urllib.parse import urlparse, urlencode

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from workers.gemini_service import generate_cover_letter, render_cover_letter_template
from workers.answer_helper import answer_application_question, save_question_answer
from workers.browser_preferences import get_headless_mode
from workers.file_utils import prepare_upload_file, safe_filename
from workers.match_utils import get_expected_salary, matches_employment_type, matches_position as position_matches, salary_matches
from failure_logger import log_failure

COOKIES_DIR = "cookies"
USER_AGENT  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

TARGET_STOPWORDS   = {"and","dan","di","the","of","for","with","in","specialist","manager","senior","junior","executive","officer","lead","head","director","coordinator","associate","assistant","analyst","consultant","supervisor","staff","engineer","administrator"}
LOCATION_STOPWORDS = {"indonesia","remote","hybrid","wfh","di"}

LINKEDIN_CARD_SELECTORS = (
    "li[data-occludable-job-id], "
    "div.base-search-card, "
    "li.jobs-search-results__list-item, "
    ".job-card-container--clickable, "
    "div[data-job-id]"
)

LOCATION_ALIASES = {
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

def storage_state_path(user_id, platform):
    return os.path.join(COOKIES_DIR, f"{user_id}_{platform}.json")

def is_linkedin_url(value: str) -> bool:
    try:
        host = urlparse(value or "").netloc.lower()
        return host == "linkedin.com" or host.endswith(".linkedin.com")
    except Exception:
        return False

def normalize_text(v: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", (v or "").lower())).strip()

def matches_position(text: str, position: str) -> bool:
    return position_matches(text, position)

def has_position_family(text: str, position: str) -> bool:
    from workers.match_utils import PURCHASING_FAMILY, normalize_text as norm_match_text
    target_words = set(norm_match_text(position).split())
    if target_words & PURCHASING_FAMILY:
        return bool(set(norm_match_text(text).split()) & PURCHASING_FAMILY)
    return matches_position(text, position)

def matches_location(job_location_raw: str, target_location: str) -> bool:
    if not job_location_raw:
        return True
    loc_clean = normalize_text(job_location_raw)
    target_key = normalize_text(target_location)
    if target_key in loc_clean:
        return True
    accepted = LOCATION_ALIASES.get(target_key, [target_key])
    return any(alias in loc_clean for alias in accepted)

async def safe_text(el_or_page, selector=None) -> str:
    try:
        el = await el_or_page.query_selector(selector) if selector else el_or_page
        return (await el.inner_text()).strip() if el else ""
    except Exception:
        return ""


class LinkedInBot:
    def __init__(self, user_id, on_apply, emit, ask_user_question=None, should_stop=None):
        self.user_id  = user_id
        self.on_apply = on_apply
        self.emit     = emit
        self.ask_user_question = ask_user_question
        self.should_stop = should_stop or (lambda: False)
        self._browser = None
        self._target_locations_by_position = {}
        self._seen_card_ids = set()

    def _progress(self, job_id, title, company, loc, step, status, msg=""):
        self.emit({
            "type": "progress", "platform": "linkedin",
            "job_id": job_id, "job_title": title, "company": company,
            "location": loc, "step": step, "status": status, "message": msg,
        })

    async def run(self, targets):
        self._target_locations_by_position = {}
        self._seen_card_ids = set()
        for target in targets:
            key = normalize_text(target.get("position") or "")
            loc = (target.get("location") or "").strip()
            if key and loc:
                self._target_locations_by_position.setdefault(key, [])
                if loc not in self._target_locations_by_position[key]:
                    self._target_locations_by_position[key].append(loc)

        state_path = storage_state_path(self.user_id, "linkedin")
        if not os.path.exists(state_path):
            self.emit({"type": "error", "platform": "linkedin",
                       "message": "Belum login LinkedIn. Login dulu di Settings."})
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=get_headless_mode(self.user_id),
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled"],
            )
            self._browser = browser
            context = await browser.new_context(storage_state=state_path, user_agent=USER_AGENT)
            page    = await context.new_page()

            self.emit({"type": "status", "platform": "linkedin",
                       "message": "Memverifikasi session LinkedIn..."})
            try:
                await page.goto("https://www.linkedin.com/feed/", timeout=60000, wait_until="domcontentloaded")
            except Exception:
                pass

            if "login" in page.url or "authwall" in page.url:
                self.emit({"type": "error", "platform": "linkedin",
                           "message": "Session expired. Login ulang di Settings."})
                await browser.close()
                return

            self.emit({"type": "status", "platform": "linkedin",
                       "message": "Session valid, mulai mencari lowongan..."})

            for target in targets:
                if self.should_stop():
                    raise asyncio.CancelledError()
                try:
                    await self._search_and_apply(page, target)
                except Exception as e:
                    self.emit({"type": "error", "platform": "linkedin", "message": f"Error: {e}"})

            await browser.close()

    async def _search_and_apply(self, page, target):
        position       = target["position"]
        location       = target["location"]
        cv_path        = target["file_path"]
        cv_name        = target.get("file_name") or os.path.basename(cv_path)
        cv_text        = target.get("cv_text", "")
        cover_template = target.get("cover_letter") or ""
        employment_type = target.get("employment_type") or "full_time"
        expected_salary = (target.get("expected_salary") or "").strip() or get_expected_salary(self.user_id)
        accepted_locations = self._target_locations_by_position.get(normalize_text(position), [location])

        self.emit({"type": "status", "platform": "linkedin",
                   "message": f"Mencari: {position} di {location}"})
        print(f"[ORDAL] LinkedInBot search START: {position} @ {location}")

        params = urlencode({
            "keywords": position,
            "location": location,
            "f_AL": "true",
            "sortBy": "DD",
        })
        url = f"https://www.linkedin.com/jobs/search/?{params}"
        try:
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await self._delay(2, 3)
            await self._ensure_easy_apply_filter(page)
        except Exception as e:
            reason = f"Search LinkedIn gagal dibuka: {str(e)[:160]}"
            print(f"[ORDAL] LinkedInBot search goto FAILED: {e}")
            self.emit({"type": "error", "platform": "linkedin", "message": reason})
            await self._record_failure(
                page, "LinkedIn Search", "LinkedIn", url, position, location,
                reason, "search_goto",
            )
            return

        self.emit({"type": "status", "platform": "linkedin",
                   "message": f"Halaman LinkedIn terbuka: {page.url[:120]}"})

        # Dismiss any login overlay (shown when session expired but page loaded)
        try:
            close_btn = await page.query_selector(
                ".modal__overlay button[aria-label='Dismiss'], "
                ".modal__overlay .artdeco-modal__dismiss, "
                ".modal__overlay svg[data-test-icon='close'], "
                "button[data-tracking-control-name='public_jobs_authwall_modal_close'], "
                "[data-test-modal-close-btn]"
            )
            if close_btn and await close_btn.is_visible():
                await close_btn.click(force=True, timeout=3000)
                await self._delay(0.5, 1)
                print("[ORDAL] LinkedInBot dismissed login overlay")
        except Exception:
            pass

        job_counter = 0
        page_no = 1
        max_pages = 50

        while page_no <= max_pages:
            if self.should_stop():
                raise asyncio.CancelledError()

            await self._load_current_results_page(page)
            cards = await page.query_selector_all(LINKEDIN_CARD_SELECTORS)

            if not cards:
                self.emit({"type": "status", "platform": "linkedin",
                           "message": "Tidak ada lowongan ditemukan di halaman ini"})
                await self._record_failure(
                    page, "LinkedIn Search", "LinkedIn", page.url, position, location,
                    "Tidak ada kartu lowongan ditemukan", "search_no_cards",
                )
                break

            self.emit({"type": "status", "platform": "linkedin",
                       "message": f"Halaman LinkedIn {page_no}: {len(cards)} lowongan ditemukan"})

            seen_page_ids = set()
            for card_idx, card in enumerate(cards):
                if self.should_stop():
                    raise asyncio.CancelledError()
                try:
                    summary = await self._extract_card_summary(card, card_idx)
                    card_id = summary["card_id"]
                    if card_id in seen_page_ids:
                        continue
                    seen_page_ids.add(card_id)

                    if summary["real_id"]:
                        global_card_key = f"{normalize_text(position)}:{card_id}"
                        if global_card_key in self._seen_card_ids:
                            continue
                        self._seen_card_ids.add(global_card_key)

                    if summary["already_applied"]:
                        self._progress(
                            f"li_applied_{card_id}", summary["title"], summary["company"],
                            summary["location"] or location, "duplikat", "skip", "Sudah dilamar di LinkedIn",
                        )
                        await self.on_apply(
                            "linkedin", summary["title"], summary["company"], summary.get("url") or page.url,
                            position, location, "skipped", "Sudah dilamar di LinkedIn", summary["location"], "",
                        )
                        continue

                    card_text = summary["text"]
                    title_text = summary["title"] or card_text
                    if not has_position_family(title_text, position):
                        continue
                    if not matches_position(title_text, position):
                        skip_id = f"li_skip_{page_no}_{card_idx}"
                        self._progress(
                            skip_id,
                            summary["title"],
                            summary["company"],
                            summary["location"] or location,
                            "analisis", "ok", "Dicek dari kartu LinkedIn",
                        )
                        self._progress(
                            skip_id,
                            summary["title"],
                            summary["company"],
                            summary["location"] or location,
                            "kesesuaian", "skip", "Posisi tidak sesuai",
                        )
                        continue

                    employment_ok, employment_reason = matches_employment_type(card_text, employment_type)
                    if not employment_ok:
                        skip_id = f"li_skip_type_{page_no}_{card_idx}"
                        self._progress(
                            skip_id,
                            summary["title"],
                            summary["company"],
                            summary["location"] or location,
                            "analisis", "ok", "Dicek dari kartu LinkedIn",
                        )
                        self._progress(
                            skip_id,
                            summary["title"],
                            summary["company"],
                            summary["location"] or location,
                            "kesesuaian", "skip", employment_reason or "Tipe kerja tidak sesuai",
                        )
                        await self.on_apply(
                            "linkedin", summary["title"], summary["company"], summary.get("url") or page.url,
                            position, location, "skipped", employment_reason or "Tipe kerja tidak sesuai", summary["location"], "",
                        )
                        continue

                    job_counter += 1
                    job_id = f"li_{job_counter}_{random.randint(1000,9999)}"

                    # Remove login overlay from DOM (LinkedIn shows it after 1st anonymous view)
                    try:
                        await page.evaluate("""
                            document.querySelectorAll('.modal__overlay, .artdeco-modal__backdrop, '
                                + '.top-level-modal-container, [role="dialog"][aria-modal="true"], '
                                + '.artdeco-modal')
                                .forEach(function(el) { el.remove(); });
                        """)
                    except Exception:
                        pass

                    # Re-query card to avoid stale element reference
                    try:
                        fresh_cards = await page.query_selector_all(LINKEDIN_CARD_SELECTORS)
                        if card_idx < len(fresh_cards):
                            await fresh_cards[card_idx].click(timeout=5000)
                        else:
                            continue
                    except Exception:
                        continue
                    await self._delay(1.5, 2.5)

                    # If card click triggered login redirect, skip
                    if "login" in page.url or "authwall" in page.url:
                        self.emit({"type": "status", "platform": "linkedin",
                                   "message": "Session expired saat apply — login ulang di Settings."})
                        return

                    is_dup = await self._process_job(
                        page, cv_path, cv_text, position, location, job_id,
                        cover_template, cv_name, expected_salary, accepted_locations, summary,
                        employment_type,
                    )

                except Exception as e:
                    print(f"[ORDAL] LinkedInBot card loop error: {e}")
                    continue

            if not await self._go_to_next_results_page(page, page_no):
                page_info = await self._linkedin_pagination_info(page)
                detail = f"; {page_info}" if page_info else ""
                self.emit({"type": "status", "platform": "linkedin",
                           "message": f"Tidak ada halaman LinkedIn {page_no + 1}{detail}"})
                self.emit({"type": "status", "platform": "linkedin",
                           "message": f"Semua halaman LinkedIn telah dicek ({job_counter} lowongan diproses)"})
                break
            page_no += 1

        print(f"[ORDAL] LinkedInBot search DONE: {job_counter} lowongan diproses")
        self.emit({"type": "status", "platform": "linkedin",
                   "message": f"Selesai scan LinkedIn: {job_counter} lowongan diproses"})

    async def _load_current_results_page(self, page):
        for _ in range(4):
            if self.should_stop():
                raise asyncio.CancelledError()
            try:
                await page.mouse.wheel(0, 1400)
                await self._delay(0.4, 0.7)
            except Exception:
                break
        try:
            await page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

    async def _go_to_next_results_page(self, page, current_page: int) -> bool:
        next_page = current_page + 1
        selectors = [
            f'button[aria-label="Page {next_page}"]',
            f'button[aria-label="Halaman {next_page}"]',
            f'li[data-test-pagination-page-btn="{next_page}"] button',
            f'.jobs-search-pagination__pages button:has-text("{next_page}")',
            f'.artdeco-pagination button:has-text("{next_page}")',
            'button[aria-label*="Next"]',
            'button[aria-label*="Berikutnya"]',
        ]
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if not btn or not await btn.is_visible():
                    continue
                disabled = await btn.get_attribute("disabled")
                aria_disabled = await btn.get_attribute("aria-disabled")
                if disabled is not None or aria_disabled == "true":
                    continue
                await btn.scroll_into_view_if_needed()
                await btn.click(force=True, timeout=5000)
                self.emit({"type": "status", "platform": "linkedin",
                           "message": f"Pindah ke halaman LinkedIn {next_page}"})
                await self._delay(2, 3)
                return True
            except Exception:
                continue
        return False

    async def _linkedin_pagination_info(self, page) -> str:
        try:
            return await page.evaluate(
                r"""
                () => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const pag = document.querySelector('.jobs-search-pagination__pages, .artdeco-pagination, .artdeco-pagination__pages');
                    const pages = pag ? [...pag.querySelectorAll('button, li')].map(el => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || '')).filter(Boolean) : [];
                    const resultText = [...document.querySelectorAll('h1, h2, small, span, div')]
                        .map(el => clean(el.innerText || el.textContent || ''))
                        .find(t => t.length <= 140 && (/^\d+\s+(hasil|results?)$/i.test(t) || /\d+\s+(hasil|results?)/i.test(t)));
                    const next = document.querySelector('button[aria-label*="Next"], button[aria-label*="Berikutnya"]');
                    const nextDisabled = next ? (next.disabled || next.getAttribute('aria-disabled') === 'true') : null;
                    const chunks = [];
                    if (resultText) chunks.push(resultText);
                    if (pages.length) chunks.push('pagination: ' + pages.join(' '));
                    if (nextDisabled !== null) chunks.push('next=' + (nextDisabled ? 'disabled' : 'enabled'));
                    return chunks.join('; ');
                }
                """
            ) or ""
        except Exception:
            return ""

    async def _extract_card_summary(self, card, index: int) -> dict:
        try:
            data = await card.evaluate(
                r"""
                (el, index) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const pick = sels => {
                        for (const sel of sels) {
                            const n = el.querySelector(sel);
                            const txt = clean(n && (n.innerText || n.textContent));
                            if (txt) return txt;
                        }
                        return '';
                    };
                    const href = pick(['a[href*="/jobs/view/"]']) ? (el.querySelector('a[href*="/jobs/view/"]') || {}).href || '' : '';
                    let cardId = el.getAttribute('data-job-id') || el.getAttribute('data-occludable-job-id') || el.getAttribute('data-entity-urn') || el.getAttribute('data-urn') || '';
                    let realId = !!cardId;
                    if (!cardId && href) {
                        const m = href.match(/\/jobs\/view\/(\d+)/) || href.match(/[?&]currentJobId=(\d+)/);
                        if (m) { cardId = m[1]; realId = true; }
                    }
                    const rawText = el.innerText || el.textContent || '';
                    const text = clean(rawText);
                    const lines = rawText.split(/\n|\r/).map(clean).filter(Boolean);
                    const title = pick([
                        '.job-card-list__title', '.job-card-container__link', '.base-search-card__title',
                        'a[href*="/jobs/view/"]', '[class*="job-title"]'
                    ]) || lines[0] || 'Lowongan LinkedIn';
                    let company = pick([
                        '.job-card-container__primary-description', '.base-search-card__subtitle',
                        '.artdeco-entity-lockup__subtitle', '[class*="company"]'
                    ]);
                    let location = pick([
                        '.job-card-container__metadata-item', '.base-search-card__metadata',
                        '.job-search-card__location', '[class*="metadata-item"]'
                    ]);
                    const subtitle = pick(['.artdeco-entity-lockup__subtitle']);
                    if (subtitle && subtitle.includes(' · ')) {
                        const parts = subtitle.split(' · ').map(clean).filter(Boolean);
                        if (!company || company === subtitle) company = parts[0] || '';
                        location = location || parts.slice(1).join(' · ');
                    }
                    if (!company && lines.length > 1) company = lines[1];
                    if (!location && lines.length > 2) location = lines.slice(2, 5).find(t => /jakarta|bekasi|cikarang|indonesia|remote|hybrid|on-site|onsite/i.test(t)) || '';
                    const footer = clean((el.querySelector('.job-card-container__footer-job-state, [class*="footer-job-state"]') || {}).innerText || '');
                    const alreadyApplied = /applied|dilamar|lamaran terkirim/i.test(footer || text);
                    if (!cardId) cardId = 'fallback-' + index + '-' + Array.from(text).reduce((h, ch) => ((h << 5) - h + ch.charCodeAt(0)) | 0, 0);
                    return { cardId, realId, title, company: company || 'Unknown', location, alreadyApplied, text, url: href };
                }
                """,
                index,
            )
            return {
                "card_id": str(data.get("cardId") or f"fallback-{index}"),
                "real_id": bool(data.get("realId")),
                "title": data.get("title") or "Lowongan LinkedIn",
                "company": data.get("company") or "Unknown",
                "location": self._clean_linkedin_location(data.get("location") or ""),
                "already_applied": bool(data.get("alreadyApplied")),
                "text": data.get("text") or "",
                "url": data.get("url") or "",
            }
        except Exception:
            text = await safe_text(card)
            return {
                "card_id": f"fallback-{index}-{hash(text)}",
                "real_id": False,
                "title": (text.splitlines()[0].strip() if text.splitlines() else "Lowongan LinkedIn"),
                "company": "Unknown",
                "location": "",
                "already_applied": "applied" in normalize_text(text) or "dilamar" in normalize_text(text),
                "text": text,
                "url": "",
            }

    def _clean_linkedin_location(self, raw: str) -> str:
        text = re.sub(r"\s+", " ", raw or "").strip(" ·|,-")
        if self._is_bad_location_text(text):
            return ""
        parts = [p.strip(" ·|,-") for p in re.split(r"\s+·\s+|\n|\|", text) if p.strip()]
        for part in parts:
            if not self._is_bad_location_text(part) and re.search(r"jakarta|bekasi|cikarang|indonesia|remote|hybrid|on-site|onsite", part, re.I):
                return part
        return text

    def _is_bad_location_text(self, text: str) -> bool:
        clean = normalize_text(text)
        if not clean:
            return True
        bad_phrases = [
            "kota negara bagian atau kode pos",
            "city state or zip",
            "search jobs",
            "cari lowongan",
            "tambahkan lokasi",
            "add location",
        ]
        return len(clean) > 90 or any(p in clean for p in bad_phrases)

    def _field_type_from_label(self, label: str, fallback: str = "text") -> str:
        clean = normalize_text(label)
        if fallback == "number" or any(part in clean for part in (
            "salary", "gaji", "years", "year", "bulan", "month", "notice period",
            "calendar days", "days", "hari", "decimal number", "larger than", "experience",
        )):
            return "number"
        return fallback or "text"

    async def _ensure_easy_apply_filter(self, page):
        try:
            if "f_AL=true" not in (page.url or ""):
                sep = "&" if "?" in page.url else "?"
                await page.goto(f"{page.url}{sep}f_AL=true", timeout=60000, wait_until="domcontentloaded")
                await self._delay(1, 1.5)
            text = normalize_text(await safe_text(page, "body"))
            if "melamar mudah" in text or "easy apply" in text:
                self.emit({"type": "status", "platform": "linkedin",
                           "message": "Filter LinkedIn Easy Apply aktif"})
        except Exception as e:
            self.emit({"type": "status", "platform": "linkedin",
                       "message": f"Filter Easy Apply tidak bisa diverifikasi: {str(e)[:60]}"})

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

    async def _already_applied_on_linkedin(self, page) -> bool:
        try:
            return bool(await page.evaluate(
                r"""
                () => {
                    const clean = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const roots = [
                        ...document.querySelectorAll(
                            '.job-details-jobs-unified-top-card, .jobs-unified-top-card, '
                            + '.jobs-details__main-content, .jobs-search__job-details--container, '
                            + '[class*="jobs-unified-top-card"], [class*="job-details"]'
                        )
                    ];
                    const scopes = roots.length ? roots.slice(0, 4) : [document.body];
                    const appliedWords = ['dilamar', 'applied', 'application submitted', 'lamaran terkirim'];
                    const easyWords = ['melamar mudah', 'easy apply'];
                    for (const root of scopes) {
                        const visibleText = clean(root.innerText || root.textContent || '');
                        const hasApplied = appliedWords.some(w => visibleText.includes(w));
                        if (!hasApplied) continue;
                        const buttons = [...root.querySelectorAll('button, a, [role="button"]')].map(el => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || ''));
                        const hasEasyButton = buttons.some(t => easyWords.some(w => t.includes(w)));
                        const hasAppliedButton = buttons.some(t => appliedWords.some(w => t.includes(w)));
                        if (hasAppliedButton || !hasEasyButton) return true;
                    }
                    return false;
                }
                """
            ))
        except Exception:
            return False

    async def _easy_apply_modal_visible(self, page) -> bool:
        try:
            modal = await page.query_selector(".jobs-easy-apply-modal")
            return bool(modal and await modal.is_visible())
        except Exception:
            return False

    async def _apply_click_left_linkedin(self, page, pages_before: int) -> bool:
        left_linkedin = False
        try:
            new_pages = page.context.pages[pages_before:]
        except Exception:
            new_pages = []

        for opened_page in new_pages:
            try:
                await opened_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            try:
                if not is_linkedin_url(opened_page.url):
                    left_linkedin = True
            except Exception:
                left_linkedin = True
            try:
                await opened_page.close()
            except Exception:
                pass

        if not is_linkedin_url(page.url):
            left_linkedin = True
        return left_linkedin

    async def _process_job(self, page, cv_path, cv_text, position, location,
                           job_id, cover_template="", cv_name="", expected_salary="",
                           accepted_locations=None, card_summary=None, employment_type="full_time") -> bool:
        """Returns True if duplicate."""
        job_title = "Unknown"; company = "Unknown"; job_location = ""; salary = ""
        card_summary = card_summary or {}
        try:
            # LinkedIn 2025+ class names + legacy fallbacks
            detail_title = (
                await safe_text(page, "h1.t-24") or
                await safe_text(page, ".job-details-jobs-unified-top-card__job-title h1") or
                await safe_text(page, ".jobs-unified-top-card__job-title h1") or
                await safe_text(page, ".base-search-card__title") or
                await safe_text(page, "h1.job-title") or
                await safe_text(page, "[class*='job-title'] h1") or
                ""
            )
            detail_company = (
                await safe_text(page, ".job-details-jobs-unified-top-card__company-name a") or
                await safe_text(page, ".jobs-unified-top-card__company-name a") or
                await safe_text(page, ".base-search-card__subtitle") or
                await safe_text(page, "[class*='company-name']") or
                ""
            )
            detail_location = (
                await safe_text(page, ".job-details-jobs-unified-top-card__primary-description-container .tvm__text") or
                await safe_text(page, ".jobs-unified-top-card__bullet") or
                await safe_text(page, ".job-search-card__location") or
                ""
            )
            job_title = detail_title or card_summary.get("title") or "Unknown"
            company = detail_company or card_summary.get("company") or "Unknown"
            job_location = self._clean_linkedin_location(detail_location) or card_summary.get("location") or ""
            if self._is_bad_location_text(job_location):
                job_location = card_summary.get("location") or ""
            salary = (
                await safe_text(page, "[class*='salary']") or
                await safe_text(page, "[data-test-job-salary]") or
                ""
            )
            detail_text = await safe_text(page, ".jobs-description-content, .jobs-box__html-content, [class*='description']") or ""
            job_url = card_summary.get("url") or page.url

            self._progress(job_id, job_title, company, job_location or location, "analisis", "running")
            await self._delay(0.2, 0.4)
            self._progress(job_id, job_title, company, job_location or location, "analisis", "ok")

            # Kesesuaian — hanya cocokkan berdasarkan job title (bukan description)
            self._progress(job_id, job_title, company, job_location or location, "kesesuaian", "running")
            pos_ok = matches_position(job_title, position)
            loc_ok, loc_reason = self._matches_target_location(job_location, location, accepted_locations or [])
            salary_ok, salary_reason = salary_matches(expected_salary, salary)
            employment_ok, employment_reason = matches_employment_type(
                " ".join([job_title, detail_text, card_summary.get("text") or ""]), employment_type,
            )

            if not pos_ok or not loc_ok or not salary_ok or not employment_ok:
                reason = "Posisi tidak sesuai" if not pos_ok else ("Lokasi tidak sesuai" if not loc_ok else (salary_reason if not salary_ok else employment_reason))
                self._progress(job_id, job_title, company, job_location or location,
                               "kesesuaian", "skip", reason)
                await self.on_apply("linkedin", job_title, company, job_url,
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
                await self.on_apply("linkedin", job_title, company, job_url,
                                    position, location, "skipped", "Sudah pernah dilamar", job_location, salary)
                return True

            if await self._already_applied_on_linkedin(page):
                self._progress(job_id, job_title, company, job_location or location,
                               "duplikat", "skip", "Sudah dilamar di LinkedIn")
                await self.on_apply("linkedin", job_title, company, job_url,
                                    position, location, "skipped", "Sudah dilamar di LinkedIn", job_location, salary)
                return True
            self._progress(job_id, job_title, company, job_location or location, "duplikat", "ok")

            # Apply — 3-stage Easy Apply detection (from reference bot)
            self._progress(job_id, job_title, company, job_location or location, "apply", "running")

            is_easy_apply = False

            # Stage 1: Classic Easy Apply button with aria-label
            for sel in [
                "button.jobs-apply-button[aria-label*='Easy']",
                "button[aria-label*='Easy Apply']",
                "button.jobs-apply-button.artdeco-button--3",
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        pages_before = len(page.context.pages)
                        await btn.click(force=True, timeout=5000)
                        await self._delay(1, 2)
                        if await self._apply_click_left_linkedin(page, pages_before):
                            await self._handle_external_apply_redirect(
                                page, job_id, job_title, company, job_location or location,
                                job_url, position, location, salary,
                            )
                            return False
                        if await self._easy_apply_modal_visible(page):
                            is_easy_apply = True
                            break
                except Exception:
                    pass

            # Stage 2: URL pattern detection (Easy Apply link)
            if not is_easy_apply:
                try:
                    ea_link = await page.query_selector("a[href*='openSDUIApplyFlow=true']")
                    if ea_link and await ea_link.is_visible():
                        pages_before = len(page.context.pages)
                        await ea_link.click(force=True, timeout=5000)
                        await self._delay(1, 2)
                        if await self._apply_click_left_linkedin(page, pages_before):
                            await self._handle_external_apply_redirect(
                                page, job_id, job_title, company, job_location or location,
                                job_url, position, location, salary,
                            )
                            return False
                        if await self._easy_apply_modal_visible(page):
                            is_easy_apply = True
                            print(f"[ORDAL] LinkedInBot Easy Apply via URL pattern: {job_title}")
                except Exception:
                    pass

            # Stage 3: Generic apply button + check for modal vs external
            if not is_easy_apply:
                try:
                    generic_btn = await page.query_selector("button.jobs-apply-button")
                    if generic_btn and await generic_btn.is_visible():
                        pages_before = len(page.context.pages)
                        await generic_btn.click(force=True, timeout=5000)
                        await self._delay(1, 2)
                        pages_after = len(page.context.pages)
                        if pages_after > pages_before:
                            # New tab opened = external apply, close it
                            self._progress(job_id, job_title, company, job_location or location,
                                           "apply", "skip", "Redirect eksternal, balik ke LinkedIn Jobs")
                            await self.on_apply("linkedin", job_title, company, job_url,
                                                position, location, "skipped", "Redirect eksternal", job_location, salary)
                            for p in page.context.pages[pages_before:]:
                                try: await p.close()
                                except: pass
                            return False
                        if not is_linkedin_url(page.url):
                            await self._handle_external_apply_redirect(
                                page, job_id, job_title, company, job_location or location,
                                job_url, position, location, salary,
                            )
                            return False
                        # Check if modal appeared
                        modal = await page.query_selector(".jobs-easy-apply-modal")
                        if modal:
                            is_easy_apply = True
                            print(f"[ORDAL] LinkedInBot Easy Apply via modal detection: {job_title}")
                        else:
                            await page.keyboard.press("Escape")
                except Exception:
                    pass

            if not is_easy_apply:
                print(f"[ORDAL] LinkedInBot NO Easy Apply: {job_title} @ {company}")
                self._progress(job_id, job_title, company, job_location or location,
                               "apply", "fail", "Tidak ada Easy Apply")
                await self.on_apply("linkedin", job_title, company, job_url,
                                    position, location, "skipped", "No Easy Apply", job_location, salary)
                return False

            await self._delay(1, 2)
            print(f"[ORDAL] LinkedInBot Easy Apply opened: {job_title}")

            success = await self._complete_easy_apply(
                page, cv_path, cv_text, job_title, company, position, cover_template, cv_name
            )

            if success:
                self._progress(job_id, job_title, company, job_location or location, "apply", "ok")
                await self.on_apply("linkedin", job_title, company, job_url,
                                    position, location, "applied", None, job_location, salary)
                return False
            else:
                self._progress(job_id, job_title, company, job_location or location,
                               "apply", "fail", "Submit tidak terkonfirmasi")
                await self._record_failure(
                    page, job_title, company, job_url, position, location,
                    "Submit tidak terkonfirmasi", "easy_apply_submit",
                )
                await self.on_apply("linkedin", job_title, company, job_url,
                                    position, location, "failed", "Submit tidak terkonfirmasi", job_location, salary)
                # Discard via Escape then click Discard span
                try:
                    await page.keyboard.press("Escape")
                    await self._delay(0.3, 0.5)
                    discard = await page.query_selector('span:has-text("Discard")')
                    if not discard:
                        discard = await page.query_selector(
                            '[data-control-name="discard_application_confirm_btn"], '
                            'button[aria-label="Discard"]'
                        )
                    if discard:
                        await discard.click(force=True, timeout=3000)
                except Exception:
                    pass
                return False

        except PlaywrightTimeout as e:
            self._progress(job_id, job_title, company, location, "apply", "fail",
                           f"Timeout: {str(e)[:60]}")
            await self._record_failure(
                page, job_title, company, page.url, position, location,
                f"Timeout: {str(e)[:120]}", "process_job_timeout",
            )
            return False
        except Exception as e:
            print(f"[ORDAL] LinkedInBot _process_job error: {e}")
            self._progress(job_id, job_title, company, location, "apply", "fail", str(e)[:60])
            await self._record_failure(
                page, job_title, company, page.url, position, location,
                str(e)[:200], "process_job_exception",
            )
            return False

    # ── Easy Apply flow: modal steps, data-test-form-element questions ──

    async def _complete_easy_apply(self, page, cv_path, cv_text, job_title, company,
                                   position, cover_template: str = "", cv_name: str = "") -> bool:
        """Handle multi-step Easy Apply using reference bot's proven selectors."""
        uploaded_resume = False
        self._current_cover_template = cover_template or ""
        self._current_company = company or ""
        try:
            next_counter = 0
            last_step_text = ""
            stagnant_steps = 0
            while next_counter < 15:
                next_counter += 1
                await self._delay(0.5, 1)
                try:
                    modal = await page.query_selector(".jobs-easy-apply-modal")
                    step_text = normalize_text(await modal.inner_text())[:500] if modal else ""
                    if step_text and step_text == last_step_text:
                        stagnant_steps += 1
                    else:
                        stagnant_steps = 0
                        last_step_text = step_text
                    if stagnant_steps >= 3:
                        print("[ORDAL] LinkedInBot stagnant modal step")
                        return False
                except Exception:
                    pass

                # 1. Upload resume ONCE
                if not uploaded_resume:
                    if await self._select_existing_resume(page, cv_name or os.path.basename(cv_path)):
                        uploaded_resume = True
                        self.emit({"type": "status", "platform": "linkedin",
                                   "message": f"CV dipilih: {cv_name or os.path.basename(cv_path)}"})
                        await self._delay(0.2, 0.4)
                        continue
                    try:
                        fi = await page.query_selector('input[type="file"], input[name="file"]')
                        if fi:
                            await fi.set_input_files(prepare_upload_file(cv_path, cv_name))
                            uploaded_resume = True
                            self.emit({"type": "status", "platform": "linkedin",
                                       "message": f"CV diunggah: {cv_name or os.path.basename(cv_path)}"})
                            await self._delay(0.5, 1)
                    except Exception:
                        pass

                    if await self._resume_required_visible(page):
                        await self._record_failure(
                            page, job_title, company, page.url, position, "",
                            "CV belum terpilih", "resume_select",
                        )
                        return False

                # 2. Answer all questions using data-test-form-element (reference bot approach)
                try:
                    questions = await page.query_selector_all("div[data-test-form-element]")
                    print(f"[ORDAL] LinkedInBot questions found: {len(questions)}")
                    for q in questions:
                        await self._handle_question_v2(q, cv_text, job_title)
                except Exception as e:
                    print(f"[ORDAL] LinkedInBot questions error: {e}")

                # 2b. LinkedIn contact-info steps can live outside data-test-form-element.
                await self._fill_linkedin_global_fields(page, cv_text, job_title)

                # 3. Workable/LinkedIn fields: Headline, Summary, Cover letter.
                await self._fill_profile_and_cover_fields(page, cv_text, job_title, company, cover_template)

                # 4. Review step before submit.
                review_btn = await self._find_easy_apply_button(page, [
                    'button:has-text("Review")',
                    'button:has-text("Tinjau")',
                    'button[aria-label*="Review"]',
                    'button[aria-label*="Tinjau"]',
                ])

                if review_btn and await review_btn.is_visible():
                    try:
                        await review_btn.scroll_into_view_if_needed()
                        await review_btn.click(force=True, timeout=5000)
                        await self._delay(1, 2)
                        if await self._try_submit(page):
                            return True
                        break
                    except Exception as e:
                        print(f"[ORDAL] LinkedInBot Review click failed: {e}")
                        break

                # 5. Try Submit directly
                if await self._try_submit(page):
                    return True

                # 6. Click Next/Continue.
                next_btn = await self._find_easy_apply_button(page, [
                    'button:has-text("Next")',
                    'button:has-text("Continue")',
                    'button:has-text("Berikutnya")',
                    'button:has-text("Lanjut")',
                    'button[aria-label="Continue to next step"]',
                    'button[aria-label="Next"]',
                    'button[aria-label*="Berikutnya"]',
                    'button[aria-label*="Lanjut"]',
                ])

                if next_btn:
                    try:
                        if await self._resume_required_visible(page) and not await self._resume_selected(page):
                            print("[ORDAL] LinkedInBot blocked NEXT: resume not selected")
                            await self._record_failure(
                                page, job_title, company, page.url, position, "",
                                "CV belum terpilih sebelum Berikutnya", "resume_before_next",
                            )
                            return False
                        await next_btn.scroll_into_view_if_needed()
                        await next_btn.click(force=True, timeout=5000)
                        print(f"[ORDAL] LinkedInBot NEXT clicked step={next_counter}")
                        await self._delay(0.5, 1)
                        continue
                    except Exception as e:
                        print(f"[ORDAL] LinkedInBot Next click failed: {e}")
                        break
                else:
                    if await self._try_submit(page):
                        return True
                    break

            return False
        except Exception as e:
            print(f"[ORDAL] LinkedInBot _complete_easy_apply error: {e}")
            return False

    async def _resume_required_visible(self, page) -> bool:
        try:
            modal = await page.query_selector(".jobs-easy-apply-modal")
            text = normalize_text(await modal.inner_text()) if modal else normalize_text(await safe_text(page, "body"))
            return "resume" in text and (
                "a resume is required" in text or
                "resume is required" in text or
                "cv is required" in text or
                "wajib" in text or
                "diperlukan" in text
            )
        except Exception:
            return False

    async def _resume_selected(self, page) -> bool:
        try:
            return bool(await page.evaluate(
                r"""
                () => {
                    const modal = document.querySelector('.jobs-easy-apply-modal') || document;
                    const clean = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const text = clean(modal.innerText || '');
                    if (!text.includes('resume') && !text.includes('cv')) return false;
                    if (modal.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) return true;
                    if (/resume selected|selected resume|resume terpilih|cv terpilih|dipilih/.test(text)) return true;
                    const chooseButtons = [...modal.querySelectorAll('button, [role="button"]')]
                        .map(el => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || ''))
                        .filter(t => /pilih resume|choose resume|select resume/.test(t));
                    if (text.includes('a resume is required') && chooseButtons.length === 0) return true;
                    const required = text.includes('a resume is required') || text.includes('resume is required') || text.includes('cv is required');
                    return !required;
                }
                """
            ))
        except Exception:
            return False

    async def _selected_resume_matches(self, page, cv_name: str) -> bool:
        wanted = safe_filename(cv_name)
        if not wanted:
            return False
        try:
            return bool(await page.evaluate(
                r"""
                (wanted) => {
                    const modal = document.querySelector('.jobs-easy-apply-modal') || document;
                    const clean = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const needle = clean(wanted);
                    const relaxed = needle.replace(/[_-]+/g, ' ');
                    const hasWanted = t => t.includes(needle) || t.replace(/[_-]+/g, ' ').includes(relaxed);
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 10 && r.height > 10;
                    };
                    const textOf = el => clean(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                    const selectedMarker = el => {
                        if (!el) return false;
                        if (el.matches?.('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) return true;
                        if (el.querySelector?.('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) return true;
                        const t = textOf(el);
                        return /resume selected|selected resume|resume terpilih|cv terpilih|dipilih|terpilih/.test(t);
                    };
                    const rows = [...modal.querySelectorAll('label, li, section, div')]
                        .filter(visible)
                        .filter(el => textOf(el).includes('.pdf') && hasWanted(textOf(el)))
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        });
                    for (const row of rows) {
                        if (selectedMarker(row)) return true;
                    }
                    const body = textOf(modal);
                    const required = /a resume is required|resume is required|cv is required|wajib|diperlukan/.test(body);
                    const wantedVisible = hasWanted(body);
                    const checkedAny = !!modal.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]');
                    if (wantedVisible && !required && checkedAny && rows.length <= 1) return true;
                    return false;
                }
                """,
                wanted,
            ))
        except Exception:
            return False

    async def _select_existing_resume(self, page, cv_name: str) -> bool:
        wanted = safe_filename(cv_name)
        if not wanted:
            return False
        try:
            if await self._selected_resume_matches(page, wanted):
                print(f"[ORDAL] LinkedInBot resume already selected: {wanted}")
                return True
            try:
                more = await self._find_easy_apply_button(page, [
                    'button:has-text("Tampilkan")',
                    'button:has-text("resume lainnya")',
                    'button:has-text("Show")',
                    'button:has-text("more resumes")',
                ])
                if more and await more.is_visible():
                    await more.click(force=True, timeout=3000)
                    await self._delay(0.4, 0.7)
            except Exception:
                pass
            clicked = await page.evaluate(
                """
                (wanted) => {
                    const modal = document.querySelector('.jobs-easy-apply-modal') || document;
                    const clean = s => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                    const needle = clean(wanted);
                    const relaxed = needle.replace(/[_-]+/g, ' ');
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 10 && r.height > 10;
                    };
                    const textOf = el => clean(el.innerText || el.textContent || '');
                    const hasWanted = t => t.includes(needle) || t.replace(/[_-]+/g, ' ').includes(relaxed);
                    const clickEl = el => {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        el.click();
                    };
                    const directButtons = [...modal.querySelectorAll('button, [role="button"]')]
                        .filter(visible)
                        .filter(el => /pilih resume|choose resume|select resume/i.test(el.innerText || el.textContent || el.getAttribute('aria-label') || ''));
                    for (const btn of directButtons) {
                        const scope = btn.closest('li, section, div') || btn.parentElement || btn;
                        if (scope.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) continue;
                        if (hasWanted(textOf(btn)) || hasWanted(textOf(scope))) { clickEl(btn); return true; }
                    }
                    const rows = [...modal.querySelectorAll('label, li, div, section')]
                        .filter(visible)
                        .filter(el => {
                            const t = textOf(el);
                            return t.includes('.pdf') && hasWanted(t);
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        });
                    const fallbackRows = rows.length ? rows : [...modal.querySelectorAll('label, li, div, section')]
                        .filter(visible)
                        .filter(el => textOf(el).includes('.pdf'))
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        });
                    for (const row of fallbackRows) {
                        if (row.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]')) {
                            if (hasWanted(textOf(row))) return true;
                            continue;
                        }
                        const input = row.querySelector('input[type="radio"], input[type="checkbox"]');
                        if (input) { input.click(); return true; }
                        const radio = row.querySelector('[role="radio"], [aria-checked]');
                        if (radio) { radio.click(); return true; }
                        const choose = [...row.querySelectorAll('button, [role="button"], span, div')]
                            .find(el => /pilih resume|choose resume|select resume/i.test(el.innerText || el.textContent || ''));
                        if (choose) { clickEl(choose); return true; }
                        const label = row.closest('label') || row.querySelector('label');
                        if (label) { label.click(); return true; }
                        const r = row.getBoundingClientRect();
                        const targets = [
                            document.elementFromPoint(r.right - 28, r.top + r.height / 2),
                            document.elementFromPoint(r.right - 55, r.top + r.height / 2),
                            row,
                        ].filter(Boolean);
                        for (const target of targets) {
                            clickEl(target);
                            const checked = row.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked, [aria-checked="true"]');
                            if (checked) return true;
                        }
                    }
                    return false;
                }
                """,
                wanted,
            )
            if clicked:
                await self._delay(0.8, 1.2)
            selected = clicked and (await self._resume_selected(page) or not await self._resume_required_visible(page))
            if selected:
                print(f"[ORDAL] LinkedInBot selected existing resume: {wanted}")
            return bool(selected)
        except Exception:
            return False

    async def _try_submit(self, page) -> bool:
        """Click Submit application, then confirm LinkedIn success screen."""
        try:
            btn = await self._find_easy_apply_button(page, [
                'button:has-text("Submit application")',
                'button:has-text("Submit")',
                'button:has-text("Kirim lamaran")',
                'button:has-text("Kirim")',
                'button[aria-label="Submit application"]',
                'button[aria-label="Submit"]',
                'button[aria-label*="Kirim lamaran"]',
                'button[aria-label*="Kirim"]',
                'footer button[type="submit"]',
            ])

            if btn:
                print(f"[ORDAL] LinkedInBot SUBMIT clicking...")
                await btn.scroll_into_view_if_needed()
                await btn.click(force=True, timeout=8000)
                await self._delay(2, 3)
                print(f"[ORDAL] LinkedInBot SUBMIT done")
                if not await self._submission_confirmed(page):
                    return False
                # Click Done / Escape
                try:
                    done = await self._find_easy_apply_button(page, [
                        'button:has-text("Done")',
                        'button:has-text("Selesai")',
                        'button[aria-label="Done"]',
                        'button[aria-label*="Selesai"]',
                    ])
                    if done and await done.is_visible():
                        await done.click(force=True, timeout=3000)
                except Exception:
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass
                return True
        except Exception as e:
            print(f"[ORDAL] LinkedInBot submit error: {e}")
        return False

    async def _find_easy_apply_button(self, page, selectors):
        modal = await page.query_selector(".jobs-easy-apply-modal")
        roots = [modal, page] if modal else [page]
        for root in roots:
            for sel in selectors:
                try:
                    btn = await root.query_selector(sel)
                    if not btn or not await btn.is_visible():
                        continue
                    disabled = await btn.get_attribute("disabled")
                    aria_disabled = await btn.get_attribute("aria-disabled")
                    if disabled is None and aria_disabled != "true":
                        return btn
                except Exception:
                    continue
        return None

    async def _fill_linkedin_global_fields(self, page, cv_text, job_title):
        modal = await page.query_selector(".jobs-easy-apply-modal")
        root = modal or page

        try:
            for sel in await root.query_selector_all("select"):
                try:
                    current = await sel.input_value()
                    if current:
                        continue
                    label = normalize_text(await self._field_label(page, sel))
                    opts = await sel.query_selector_all("option")
                    chosen = None
                    fallback = None
                    for i, opt in enumerate(opts):
                        txt_raw = (await opt.inner_text() or "").strip()
                        txt = normalize_text(txt_raw)
                        if not txt or txt in ("select an option", "select", "pilih", "choose", "-"):
                            continue
                        fallback = fallback or i
                        if "@" in txt_raw and ("email" in label or "alamat email" in label):
                            chosen = i; break
                        if ("phone" in label or "country code" in label or "kode negara" in label) and ("indonesia" in txt or "+62" in txt_raw):
                            chosen = i; break
                    if chosen is None:
                        chosen = fallback
                    if chosen is not None:
                        await sel.select_option(index=chosen)
                        chosen_text = ""
                        try:
                            chosen_text = (await opts[chosen].inner_text() or "").strip()
                        except Exception:
                            pass
                        if chosen_text:
                            save_question_answer(self.user_id, "linkedin", label or "LinkedIn dropdown", chosen_text, "dropdown", source="observed")
                        await self._delay(0.1, 0.25)
                except Exception:
                    continue
        except Exception:
            pass

        try:
            fields = await root.query_selector_all(
                'input[type="text"], input[type="tel"], input[type="email"], input[type="number"], input:not([type])'
            )
            for field in fields:
                try:
                    if await field.input_value():
                        continue
                    label = await self._field_label(page, field)
                    if not label:
                        continue
                    ft = self._field_type_from_label(label, await field.get_attribute("type") or "text")
                    ans = await answer_application_question(self.user_id, "linkedin", label, ft, cv_text, job_title, self.ask_user_question)
                    if ans:
                        await field.fill(ans)
                        await self._delay(0.1, 0.25)
                except Exception:
                    continue
        except Exception:
            pass

    def _resume_headline(self, cv_text: str, job_title: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in (cv_text or "").splitlines()]
        lines = [line for line in lines if line]
        for line in lines[:40]:
            clean = normalize_text(line)
            if 12 <= len(line) <= 120 and any(word in clean for word in ["purchasing", "procurement", "supply chain", "buyer", "sourcing"]):
                return line[:120]
        return f"{job_title} candidate with purchasing, procurement, and vendor management experience"[:120]

    def _resume_summary(self, cv_text: str, job_title: str) -> str:
        text = re.sub(r"\s+", " ", cv_text or "").strip()
        if not text:
            return f"Experienced candidate for {job_title}, with background in purchasing, procurement, vendor coordination, negotiation, and administrative support."
        sentences = re.split(r"(?<=[.!?])\s+", text)
        picked = []
        for sentence in sentences:
            clean = normalize_text(sentence)
            if any(word in clean for word in ["purchasing", "procurement", "vendor", "supplier", "supply", "negotiation", "purchase"]):
                picked.append(sentence.strip())
            if len(" ".join(picked)) >= 450:
                break
        if not picked:
            picked = sentences[:4]
        summary = " ".join(picked).strip()
        return summary[:900]

    async def _cover_letter_text(self, page, cv_text: str, job_title: str, company: str, cover_template: str) -> str:
        cl = render_cover_letter_template(cover_template, job_title, company)
        if cl:
            return cl
        try:
            jd_el = await page.query_selector(".jobs-description__content")
            jd_text = await jd_el.inner_text() if jd_el else ""
        except Exception:
            jd_text = ""
        return await generate_cover_letter(job_title, company, jd_text, cv_text)

    async def _fill_profile_and_cover_fields(self, page, cv_text, job_title, company, cover_template):
        try:
            modal = await page.query_selector(".jobs-easy-apply-modal")
            root = modal or page
            cover_text = await self._cover_letter_text(page, cv_text, job_title, company, cover_template)
            headline = self._resume_headline(cv_text, job_title)
            summary = self._resume_summary(cv_text, job_title)
            fields = await root.query_selector_all('input[type="text"], input:not([type]), textarea')
            for field in fields:
                try:
                    current = await field.input_value()
                    if current and current.strip():
                        continue
                    label = normalize_text(await self._field_label(page, field))
                    name = normalize_text(
                        (await field.get_attribute("name") or "") + " " +
                        (await field.get_attribute("id") or "") + " " +
                        (await field.get_attribute("aria-label") or "") + " " +
                        (await field.get_attribute("placeholder") or "")
                    )
                    key = f"{label} {name}"
                    value = ""
                    if "cover letter" in key or "surat lamaran" in key:
                        value = cover_text
                    elif "headline" in key or "tagline" in key:
                        value = headline
                    elif "summary" in key or "ringkasan" in key or "professional summary" in key:
                        value = summary
                    if value:
                        await field.fill(value)
                        await self._delay(0.1, 0.25)
                        print(f"[ORDAL] LinkedInBot filled field: {label or name}")
                except Exception:
                    continue
        except Exception as e:
            print(f"[ORDAL] LinkedInBot profile/cover fill error: {e}")

    async def _field_label(self, page, field) -> str:
        label_id = await field.get_attribute("aria-labelledby") or ""
        if label_id:
            chunks = []
            for part in label_id.split():
                try:
                    el = await page.query_selector(f"#{part}")
                    if el:
                        text = (await el.inner_text() or "").strip()
                        if text:
                            chunks.append(text)
                except Exception:
                    continue
            if chunks:
                return " ".join(chunks)

        field_id = await field.get_attribute("id") or ""
        if field_id:
            try:
                label_el = await page.query_selector(f'label[for="{field_id}"]')
                if label_el:
                    text = (await label_el.inner_text() or "").strip()
                    if text:
                        return text
            except Exception:
                pass

        try:
            nearby = await field.evaluate(
                r"""
                (el) => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const texts = [];
                    const prev = el.previousElementSibling;
                    if (prev) texts.push(clean(prev.innerText || prev.textContent));
                    const parent = el.parentElement;
                    if (parent) {
                        for (const child of [...parent.children]) {
                            if (child === el) break;
                            const t = clean(child.innerText || child.textContent);
                            if (t) texts.push(t);
                        }
                    }
                    const wrapper = el.closest('label, div, section, fieldset');
                    if (wrapper) {
                        const full = clean(wrapper.innerText || wrapper.textContent);
                        const value = clean(el.value || '');
                        const withoutValue = value ? full.replace(value, '').trim() : full;
                        if (withoutValue && withoutValue.length <= 160) texts.push(withoutValue);
                    }
                    return texts.filter(Boolean).sort((a, b) => a.length - b.length)[0] || '';
                }
                """
            )
            if nearby:
                return nearby.strip()
        except Exception:
            pass

        return (await field.get_attribute("aria-label") or
                await field.get_attribute("placeholder") or
                await field.get_attribute("name") or "").strip()

    async def _submission_confirmed(self, page) -> bool:
        success_phrases = [
            "application submitted", "application sent", "your application was sent",
            "your application has been submitted", "done", "successfully applied",
            "lamaran terkirim", "lamaran berhasil", "berhasil dikirim", "sudah melamar",
        ]
        failure_phrases = ["required", "please enter", "please select", "error", "wajib", "harus", "pilih", "isi"]
        for _ in range(8):
            try:
                done = await self._find_easy_apply_button(page, [
                    'button:has-text("Done")',
                    'button:has-text("Selesai")',
                    'button[aria-label="Done"]',
                    'button[aria-label*="Selesai"]',
                ])
                if done and await done.is_visible():
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

    async def _handle_external_apply_redirect(self, page, job_id, job_title, company, loc,
                                              job_url, position, location, salary):
        external_url = page.url
        self._progress(job_id, job_title, company, loc, "apply", "skip", "Redirect eksternal, balik ke LinkedIn Jobs")
        await self.on_apply(
            "linkedin", job_title, company, job_url,
            position, location, "skipped", "Redirect eksternal", loc, salary,
        )
        self.emit({
            "type": "status",
            "platform": "linkedin",
            "message": f"Lamaran eksternal ditutup: {external_url[:120]}",
        })
        try:
            await page.go_back(timeout=10000, wait_until="domcontentloaded")
            await self._delay(0.7, 1.1)
        except Exception:
            pass
        if not is_linkedin_url(page.url) and is_linkedin_url(job_url):
            try:
                await page.goto(job_url, timeout=30000, wait_until="domcontentloaded")
                await self._delay(0.7, 1.1)
            except Exception:
                pass
        modal_text = ""
        try:
            modal = await page.query_selector(".jobs-easy-apply-modal")
            modal_text = await modal.inner_text() if modal else ""
        except Exception:
            pass
        body_text = ""
        try:
            body_text = await safe_text(page, "body")
        except Exception:
            pass
        try:
            log_failure({
                "platform": "linkedin",
                "step": step,
                "reason": reason,
                "job_title": job_title,
                "company": company,
                "job_url": job_url,
                "position": position,
                "location": location,
                "page_url": page.url,
                "visible_buttons": buttons,
                "modal_text": modal_text,
                "body_text": body_text,
            })
        except Exception:
            pass

    async def _extract_label(self, q_el) -> str:
        """Extract question label using reference bot's multiple methods."""
        try:
            # Method 1: label > span.visually-hidden
            span = await q_el.query_selector("label span.visually-hidden, label .visually-hidden")
            if span:
                return await span.inner_text()
            # Method 2: legend > span
            span = await q_el.query_selector("legend span")
            if span:
                return await span.inner_text()
            # Method 3: radio button title
            span = await q_el.query_selector(
                'span[data-test-form-builder-radio-button-form-component__title]'
            )
            if span:
                return await span.inner_text()
            # Method 4: label itself
            label = await q_el.query_selector("label")
            if label:
                return await label.inner_text()
            # Method 5: legend
            legend = await q_el.query_selector("legend")
            if legend:
                return await legend.inner_text()
        except Exception:
            pass
        return ""

    async def _handle_question_v2(self, q_el, cv_text, job_title):
        """Handle one question using reference bot's type detection."""
        try:
            label = (await self._extract_label(q_el)).strip().lower()
            if not label:
                label = (await q_el.inner_text() or "").strip().lower()[:100]

            # 1. SELECT dropdown
            sel = await q_el.query_selector("select")
            if sel:
                opts = await sel.query_selector_all("option")
                if len(opts) > 1:
                    option_texts = [(await opt.inner_text() or "").strip() for opt in opts]
                    label_norm = normalize_text(label)
                    preferred = None
                    if "email" in label_norm or "alamat email" in label_norm:
                        preferred = next((i for i, txt in enumerate(option_texts) if "@" in txt), None)
                    if preferred is None and ("phone" in label_norm or "country code" in label_norm or "kode negara" in label_norm):
                        preferred = next((i for i, txt in enumerate(option_texts) if "indonesia" in normalize_text(txt) or "+62" in txt), None)
                    if preferred is None:
                        prompt = f"{label}\nOptions: " + "; ".join(t for t in option_texts if t)[:500]
                        ans = await answer_application_question(self.user_id, "linkedin", prompt, "dropdown", cv_text, job_title, self.ask_user_question)
                        ans_norm = normalize_text(ans)
                        if ans_norm:
                            preferred = next((i for i, txt in enumerate(option_texts) if ans_norm in normalize_text(txt) or normalize_text(txt) in ans_norm), None)
                    if preferred is not None:
                        await sel.select_option(index=preferred)
                        if preferred < len(option_texts):
                            save_question_answer(self.user_id, "linkedin", label, option_texts[preferred], "dropdown", source="observed")
                        return
                    for i, opt in enumerate(opts):
                        txt = (await opt.inner_text()).strip().lower()
                        if txt and normalize_text(txt) not in ("select an option", "select", "pilih", "choose", "-", "--"):
                            await sel.select_option(index=i)
                            save_question_answer(self.user_id, "linkedin", label, (await opt.inner_text() or "").strip(), "dropdown", source="observed")
                            return
                    await sel.select_option(index=1)
                    if len(option_texts) > 1:
                        save_question_answer(self.user_id, "linkedin", label, option_texts[1], "dropdown", source="observed")
                return

            # 2. RADIO buttons
            radio_fieldset = await q_el.query_selector(
                'fieldset[data-test-form-builder-radio-button-form-component="true"]'
            )
            if radio_fieldset:
                radios = await radio_fieldset.query_selector_all("input")
                if radios:
                    # Try Yes from Gemini, pick first option
                    ans = await answer_application_question(self.user_id, "linkedin", label, "yes_no", cv_text, job_title, self.ask_user_question)
                    idx = 0 if (ans and ans.lower().startswith("y")) else min(1, len(radios) - 1)
                    await radios[idx].click(force=True)
                    save_question_answer(self.user_id, "linkedin", label, "Yes" if idx == 0 else "No", "yes_no", source="observed")
                return

            # 3. TEXT input
            ti = await q_el.query_selector('input[type="text"], input:not([type])')
            if ti and not await ti.input_value():
                label_norm = normalize_text(label)
                if "headline" in label_norm or "tagline" in label_norm:
                    ans = self._resume_headline(cv_text, job_title)
                else:
                    field_type = self._field_type_from_label(label, "text")
                    ans = await answer_application_question(self.user_id, "linkedin", label, field_type, cv_text, job_title, self.ask_user_question)
                if ans:
                    await ti.fill(ans)
                return

            # 4. NUMBER input
            ni = await q_el.query_selector('input[type="number"]')
            if ni and not await ni.input_value():
                ans = await answer_application_question(self.user_id, "linkedin", label, "number", cv_text, job_title, self.ask_user_question)
                if ans:
                    await ni.fill(ans)
                return

            # 5. TEXTAREA
            ta = await q_el.query_selector("textarea")
            if ta and not await ta.input_value():
                label_norm = normalize_text(label)
                if "cover letter" in label_norm or "surat lamaran" in label_norm:
                    ans = await self._cover_letter_text(q_el, cv_text, job_title, getattr(self, "_current_company", ""), getattr(self, "_current_cover_template", ""))
                elif "summary" in label_norm or "ringkasan" in label_norm or "professional summary" in label_norm:
                    ans = self._resume_summary(cv_text, job_title)
                else:
                    ans = await answer_application_question(self.user_id, "linkedin", label, "textarea", cv_text, job_title, self.ask_user_question)
                if ans:
                    await ta.fill(ans)
                return

            # 6. CHECKBOX
            cb = await q_el.query_selector('input[type="checkbox"]')
            if cb and not await cb.is_checked():
                await cb.click(force=True)
                save_question_answer(self.user_id, "linkedin", label, "Yes", "checkbox", source="observed")
                return

        except Exception:
            pass

    async def _check_duplicate(self, job_url, job_title, company) -> bool:
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

    async def _delay(self, mn=1.0, mx=3.0):
        if self.should_stop():
            try:
                if self._browser:
                    await self._browser.close()
            except Exception:
                pass
            raise asyncio.CancelledError()
        await asyncio.sleep(random.uniform(mn, mx))
