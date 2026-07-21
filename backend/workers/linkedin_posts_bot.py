"""
LinkedIn Posts Bot — Search LinkedIn posts by position + location, find job posts, extract links, apply.

Flow:
1. Search LinkedIn posts by keyword (position + location) with date filter (last 1 month)
2. For each post card (found via JS DOM walking from "… more" buttons):
   a. Scroll card into viewport
   b. Click all "…more" buttons to expand full text (text-based matching)
   c. Read expanded text
   d. Check if already applied (stop at 3+ duplicates)
   e. Parse for job-related content
   f. Extract job application links
3. Open job links and apply via external forms or LinkedIn Easy Apply

Key: LinkedIn search results use obfuscated CSS classes. Card discovery is done via JS
DOM walking from "… more" buttons to find post containers, NOT via CSS selectors.
"""
import asyncio
import json
import os
import random
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from urllib.parse import urljoin, urlparse, quote_plus

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright
from workers.browser_launcher import launch_browser

from workers.gemini_service import render_cover_letter_template, validate_and_fix_email
from workers.answer_helper import answer_application_question, save_question_answer
from workers.browser_preferences import get_headless_mode
from workers.file_utils import prepare_upload_file, safe_filename
from workers.linkedin_post_parser import parse_linkedin_post_card, ParsedLinkedInPost, extract_emails
from workers.match_utils import matches_position as position_matches, normalize_text as _norm
from workers.linkedin_bot import matches_location, LOCATION_ALIASES
from failure_logger import log_failure

COOKIES_DIR = "cookies"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Log file for debugging
_LOG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEBUG_LOG = os.path.join(_LOG_DIR, "posts_bot_debug.log")


def _blog(msg: str):
    """Append a line to the debug log file."""
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# Keywords that indicate a post is about a job opening
HIRING_KEYWORDS = (
    "we're hiring", "we are hiring", "kami membuka", "sedang membuka",
    "lowongan", "vacancy", "vacancies", "open position", "open positions",
    "job opening", "job openings", "hiring now", "now hiring",
    "join our team", "bergabung", "we have an opening",
    "looking for", "mencari", "posisi tersedia",
    "career opportunity", "career opportunities",
    "apply now", "lamar sekarang", "send your cv", "kirim cv",
    "send your resume", "drop your cv", "submit your application",
    " dm for more", "dm me", "contact me", "hubungi saya",
    "tag someone", "tag a friend", "share this",
    "click the link", "klik link", "link in comments",
    "link in bio", "check the link", "apply via",
    "full-time", "part-time", "contract", "remote", "hybrid",
    "wfh", "onsite", "on-site", "freelance",
)


def storage_state_path(user_id, platform):
    return os.path.join(COOKIES_DIR, f"{user_id}_{platform}.json")


def _normalize(v: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", (v or "").lower())).strip()


def _contains_hiring_signal(text: str) -> bool:
    """Check if post text contains signals that it's about a job opening."""
    low = _normalize(text)
    return any(kw in low for kw in HIRING_KEYWORDS)


def _clean_one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n-–—|,.;:")


def _sender_display_name(sender_email: str) -> str:
    local = (sender_email or "").split("@", 1)[0]
    name = re.sub(r"[._+-]+", " ", local).strip()
    return name.title() if name else "Applicant"


def _candidate_full_name(cv_text: str, fallback: str = "") -> str:
    for line in (cv_text or "").splitlines()[:8]:
        candidate = _clean_one_line(line)
        if not candidate:
            continue
        if re.search(r"[@|+]|linkedin|supply|procurement|analyst|manager|engineer", candidate, re.I):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z.'-]*", candidate)
        if 2 <= len(words) <= 6:
            return " ".join(words).title()
    return _clean_one_line(fallback)


def _email_position_title(job_title: str, target_position: str) -> str:
    title = _clean_one_line(job_title)
    target = _clean_one_line(target_position)
    title = re.sub(r"\b(staff|specialist|manager|supervisor|officer|admin|engineer|analyst|executive)(?:we|with|yang|untuk|dibutuhkan|needed)\b", r"\1", title, flags=re.I)
    title = _clean_one_line(title)
    suspicious = (
        len(title) > 70 or
        re.search(r"\b(activities|specifications|requirements|responsibilities|description|complete|supplier|vendor|forecasts|management review)\b", title, re.I)
    )
    if not title or suspicious:
        return target.title() if target else "Job Application"
    return title

def _clean_job_title_candidate(value: str, target_position: str = "") -> str:
    title = _clean_one_line(value)
    title = re.sub(r"^(?:we'?re|we are|kami|sedang)?\s*(?:hiring|open(?:ing)?|lowongan|vacancy|dibutuhkan|mencari)\s*[:\-–—]?\s*", "", title, flags=re.I)
    title = re.sub(r"\b(?:we|with|yang|untuk|dibutuhkan|needed|requirements?|responsibilities|qualification|kualifikasi|penempatan|location|lokasi)\b.*$", "", title, flags=re.I)
    title = re.sub(r"\b(staff|specialist|manager|supervisor|officer|admin|engineer|analyst|executive)(?:we|with|yang|untuk|dibutuhkan|needed)\b.*$", r"\1", title, flags=re.I)
    title = _clean_one_line(title)
    if not (4 <= len(title) <= 80):
        return ""
    if re.search(r"\b(activities|specifications|requirements|responsibilities|description|complete|forecasts|management review|supplier|vendor|company|industry)\b", title, re.I):
        return ""
    if target_position and not position_matches(title, target_position):
        return ""
    return title.title()


def _clean_company_candidate(value: str) -> str:
    name = _clean_one_line(value)
    name = re.sub(r"\b(?:we|we're|is|are|currently|urgently|opening|hiring|looking|needed|membuka|lowongan)\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s+(?:we|is|are)$", "", name, flags=re.I)
    name = _clean_one_line(name)
    if not (2 <= len(name) <= 70):
        return ""
    if re.search(r"[a-z](?:We|With|And|For)$", name):
        return ""
    if re.search(
        r"\b(our client|client|industry|contract|manufacturing|company|companies|logistic|supply|procurement|purchasing|"
        r"activities|forecasts|requirements|responsibilities|description|manager|staff|specialist|consultant|headhunter|"
        r"recruitment|talent acquisition|career advisor|human resources|formerly|feed post|unknown)\b",
        name,
        re.I,
    ):
        return ""
    if len(name.split()) > 6:
        return ""
    return name


def send_email(smtp_host, smtp_port, sender_email, app_password,
               recipient_email, subject, body, attachment_path=None):
    """Send email via SMTP. Returns (success: bool, error: str | None)."""
    try:
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = recipient_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                filename = os.path.basename(attachment_path)
                part.add_header("Content-Disposition", f"attachment; filename={filename}")
                msg.attach(part)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipient_email, msg.as_string())

        return True, None
    except Exception as e:
        return False, str(e)


def _get_email_config(user_id: int) -> dict:
    """Get email config from database for a user."""
    try:
        from database import get_db
        from routers.email_config import get_email_config

        cfg = get_email_config(user_id)
        if not cfg.get("configured"):
            return {}

        db = get_db()
        row = db.execute(
            """
            SELECT COALESCE(u.name, '') AS user_name,
                   COALESCE(p.testing_email_mode, 0) AS testing_email_mode
            FROM users u
            LEFT JOIN user_preferences p ON p.user_id = u.id
            WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        db.close()

        return {
            "smtp_host": cfg["smtp_host"],
            "smtp_port": cfg["smtp_port"],
            "sender_email": cfg["sender_email"],
            "app_password": cfg["app_password"],
            "user_name": row["user_name"] if row else "",
            "testing_email_mode": bool(row["testing_email_mode"]) if row else False,
        }
    except Exception:
        pass
    return {}


class LinkedInPostsBot:
    def __init__(self, user_id, on_apply, emit, should_stop=None):
        self.user_id = user_id
        self.on_apply = on_apply
        self.emit = emit
        self.should_stop = should_stop or (lambda: False)
        self._browser = None
        self._seen_fingerprints: set[str] = set()
        self._consecutive_duplicates = 0
        self._MAX_DUPLICATES = 3  # Stop after 3 already-applied positions
        self._test_emails_sent = 0
        self._MAX_TEST_EMAILS = 3

    # ──────────────────────────────────────────────────────────────────────
    # Main entry
    # ──────────────────────────────────────────────────────────────────────

    async def run(self, targets):
        # Clear debug log
        try:
            with open(_DEBUG_LOG, "w") as f:
                f.write("=== LinkedIn Posts Bot Log ===\n\n")
        except Exception:
            pass

        self._seen_fingerprints = set()
        self._consecutive_duplicates = 0
        self._test_emails_sent = 0

        state_path = storage_state_path(self.user_id, "linkedin")
        if not os.path.exists(state_path):
            self.emit({
                "type": "error", "platform": "linkedin_posts",
                "message": "Belum login LinkedIn. Login dulu di Settings.",
            })
            return

        async with async_playwright() as p:
            browser = await launch_browser(p, headless=get_headless_mode(self.user_id))
            self._browser = browser
            context = await browser.new_context(
                storage_state=state_path, user_agent=USER_AGENT,
            )
            page = await context.new_page()

            # Verify session
            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": "Memverifikasi session LinkedIn...",
            })
            try:
                await page.goto("https://www.linkedin.com/feed/", timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(2)
            except Exception:
                pass

            _blog(f"Session URL: {page.url}")

            if "login" in page.url or "authwall" in page.url:
                self.emit({
                    "type": "error", "platform": "linkedin_posts",
                    "message": "Session expired. Login ulang di Settings.",
                })
                await browser.close()
                return

            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": "Session valid, mulai mencari LinkedIn Posts...",
            })

            await self._dismiss_overlay(page)

            for target in targets:
                if self.should_stop():
                    raise asyncio.CancelledError()
                try:
                    await self._search_posts_for_target(page, target)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _blog(f"ERROR searching posts for target: {e}")
                    self.emit({
                        "type": "error", "platform": "linkedin_posts",
                        "message": f"Error searching posts: {e}",
                    })

            await browser.close()

    # ──────────────────────────────────────────────────────────────────────
    # Search posts by keyword + location (last 1 month)
    # ──────────────────────────────────────────────────────────────────────

    def _build_search_url(self, position: str, location: str) -> str:
        keywords = f"{position} {location}".strip()
        params = (
            f"keywords={quote_plus(keywords)}"
            f"&datePosted=%22past-month%22"
            f"&origin=GLOBAL_SEARCH_HEADER"
        )
        return f"https://www.linkedin.com/search/results/content/?{params}"

    async def _search_posts_for_target(self, page, target):
        position = target.get("position") or ""
        location = target.get("location") or ""
        cv_path = target.get("file_path") or ""
        cv_text = target.get("cv_text", "")
        cover_template = target.get("cover_letter") or ""
        expected_salary = target.get("expected_salary") or ""
        email_config = _get_email_config(self.user_id)  # cache once per target
        if email_config.get("testing_email_mode") and self._test_emails_sent >= self._MAX_TEST_EMAILS:
            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": f"🛑 Testing email sudah mencapai {self._MAX_TEST_EMAILS} lowongan. Pencarian LinkedIn Posts berhenti.",
            })
            return

        search_url = self._build_search_url(position, location)
        _blog(f"Search URL: {search_url}")

        self.emit({
            "type": "status", "platform": "linkedin_posts",
            "message": f"Mencari posts: \"{position}\" di \"{location}\" (1 bulan terakhir)",
        })

        # Navigate to LinkedIn content search
        try:
            await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
            await self._delay(3, 4)
        except Exception as e:
            _blog(f"ERROR navigating to search: {e}")
            self.emit({
                "type": "error", "platform": "linkedin_posts",
                "message": f"Gagal membuka search: {str(e)[:120]}",
            })
            return

        await self._dismiss_overlay(page)
        _blog(f"Search page loaded: {page.url}")

        # Scroll and process posts
        posts_processed = 0
        max_scroll_rounds = 40
        scroll_round = 0
        no_new_rounds = 0  # count rounds with no new posts

        while scroll_round < max_scroll_rounds:
            if self.should_stop():
                raise asyncio.CancelledError()

            scroll_round += 1

            # Find all post card containers using JS DOM walking
            cards_data = await self._find_post_cards(page)
            _blog(f"Scroll {scroll_round}: found {len(cards_data)} card containers")

            if not cards_data:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"Scroll {scroll_round}: Tidak ada post ditemukan, scroll lagi...",
                })
                await self._scroll_feed(page)
                await self._delay(2, 3)
                continue

            # Get element handles for each card using data attribute (reliable)
            cards = []
            for cd in cards_data:
                try:
                    handle = await page.query_selector(f'[data-ordal-card="{cd["_idx"]}"]')
                    if handle:
                        cards.append((cd, handle))
                except Exception:
                    continue

            new_posts_this_round = 0

            for i, (cd, card_handle) in enumerate(cards):
                if self.should_stop():
                    raise asyncio.CancelledError()
                if email_config.get("testing_email_mode") and self._test_emails_sent >= self._MAX_TEST_EMAILS:
                    self.emit({
                        "type": "status", "platform": "linkedin_posts",
                        "message": f"🛑 Testing email sudah mencapai {self._MAX_TEST_EMAILS} lowongan. Pencarian LinkedIn Posts berhenti.",
                    })
                    return

                try:
                    result = await self._process_single_post(
                        page, card_handle, i, position, location,
                        cv_path, cv_text, cover_template, email_config,
                        expected_salary,
                    )
                    if result:
                        new_posts_this_round += 1
                        posts_processed += 1
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _blog(f"Card error [{i}]: {e}")
                    continue

            # Stop if 3+ consecutive already-applied positions
            if self._consecutive_duplicates >= self._MAX_DUPLICATES:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"🛑 {self._consecutive_duplicates} posisi sudah dilamar berturut-turut, pencarian berhenti.",
                })
                _blog(f"Stopping: {self._consecutive_duplicates} consecutive duplicates")
                break

            if new_posts_this_round == 0:
                no_new_rounds += 1
                # If 5 consecutive rounds with no new posts, stop
                if no_new_rounds >= 5:
                    _blog(f"Stopping: no new posts for {no_new_rounds} rounds")
                    break
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"Scroll {scroll_round}: Tidak ada post baru ({no_new_rounds}/5), scroll lagi...",
                })
            else:
                no_new_rounds = 0

            await self._scroll_feed(page)
            await self._delay(2, 3)

        self.emit({
            "type": "status", "platform": "linkedin_posts",
            "message": f"Selesai: {posts_processed} post diproses dari pencarian \"{position}\" di \"{location}\"",
        })

    # ──────────────────────────────────────────────────────────────────────
    # Find post cards using JS DOM walking
    # Returns list of dicts with _idx for element handle retrieval
    # ──────────────────────────────────────────────────────────────────────

    async def _find_post_cards(self, page) -> list:
        """
        Find all post card containers on the page using JS DOM walking.
        LinkedIn search uses obfuscated CSS classes, so we walk up from
        "… more" buttons to find the post container elements.

        Returns a list of dicts: [{"_idx": int, "tag": str, "classes": str}]
        """
        try:
            cards = await page.evaluate("""
                () => {
                    const results = [];
                    const seen = new Set();

                    // Clean up stale data attributes from previous rounds
                    document.querySelectorAll('[data-ordal-card]').forEach(el => {
                        el.removeAttribute('data-ordal-card');
                    });

                    // Find all "… more" buttons
                    const moreButtons = Array.from(document.querySelectorAll('button'))
                        .filter(b => {
                            const t = (b.textContent || '').trim();
                            return t === '… more' || t === '...more' || t === '…more' || t === '... more';
                        });

                    for (const btn of moreButtons) {
                        let container = btn;

                        // Walk up to find the post card container
                        for (let i = 0; i < 20; i++) {
                            container = container.parentElement;
                            if (!container) break;

                            const text = container.innerText || '';
                            const tag = container.tagName.toLowerCase();

                            // Skip body/html/main/section
                            if (['body', 'html', 'main', 'section'].includes(tag)) continue;

                            // A post card must:
                            // - Contain this "… more" button as descendant
                            // - Have substantial text (100+ chars)
                            // - Not be too large (< 15000 chars)
                            // - Contain "Follow" or "Ikuti" (author section)
                            const hasMoreBtn = container.querySelector('button') !== null;
                            const hasProfileLink = container.querySelector('a[href*="/in/"]') !== null;
                            if (
                                hasMoreBtn &&
                                text.length > 100 &&
                                text.length < 15000 &&
                                (text.includes('Follow') || text.includes('Ikuti')) &&
                                hasProfileLink
                            ) {
                                // Dedup by element reference
                                if (!seen.has(container)) {
                                    seen.add(container);
                                    // Mark with data attribute for reliable querySelector retrieval
                                    const cardIdx = results.length;
                                    container.setAttribute('data-ordal-card', String(cardIdx));
                                    results.push({
                                        _idx: cardIdx,
                                        tag: tag,
                                        classes: (container.className || '').toString().substring(0, 100),
                                    });
                                }
                                break;
                            }
                        }
                    }
                    return results;
                }
            """)
            _blog(f"Found {len(cards)} post cards via DOM walking")
            return cards or []
        except Exception as e:
            _blog(f"ERROR finding post cards: {e}")
            return []

    # ──────────────────────────────────────────────────────────────────────
    # Process a single post card
    # ──────────────────────────────────────────────────────────────────────

    async def _process_single_post(
        self, page, card, index, position, location,
        cv_path, cv_text, cover_template, email_config=None,
        expected_salary="",
    ) -> bool:
        # ── Step 1: Scroll card into viewport ──
        try:
            await card.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)
        except Exception:
            pass

        # ── Step 2: Click "more" buttons to expand FULL text ──
        await self._expand_card_text(page, card)

        # ── Step 3: Read the card's HTML and text ──
        card_text = ""
        card_html = ""
        try:
            card_text = await page.evaluate(
                """el => {
                    const clone = el.cloneNode(true);
                    // Remove only action/reaction buttons, not all buttons
                    clone.querySelectorAll(
                        '[class*="reactions"], [class*="comment"], ' +
                        '[class*="social-actions"], [class*="react-button"], ' +
                        '[class*="overflow-menu"], [class*="follow-button"]'
                    ).forEach(n => n.remove());
                    return clone.innerText || clone.textContent || '';
                }""",
                card,
            )
            card_text = re.sub(r"\s+", " ", (card_text or "").strip())
        except Exception:
            pass

        try:
            card_html = await page.evaluate(
                "el => el.outerHTML || ''",
                card,
            )
        except Exception:
            card_html = ""

        if not card_text or len(card_text) < 20:
            _blog(f"  Card [{index}]: text too short ({len(card_text)} chars), skipping")
            return False

        _blog(f"  Card [{index}]: text length={len(card_text)}, preview={card_text[:120]}...")

        # ── Step 4: Check for duplicates ──
        parsed = parse_linkedin_post_card(card_html, card_text)
        fp = parsed.fingerprint
        if fp and fp in self._seen_fingerprints:
            self._consecutive_duplicates += 1
            _blog(f"  Card [{index}]: duplicate fingerprint, consecutive={self._consecutive_duplicates}")
            return False
        if fp:
            self._seen_fingerprints.add(fp)
        self._consecutive_duplicates = 0  # reset on new post

        # ── Step 5: Emit status ──
        author = parsed.author or "Unknown"
        self.emit({
            "type": "status", "platform": "linkedin_posts",
            "message": f"📋 [{index + 1}] Post dari {author}: {card_text[:100]}...",
        })

        # ── Step 6: Check if post is job-related ──
        if not _contains_hiring_signal(card_text):
            _blog(f"  Card [{index}]: no hiring signal")
            return False

        self.emit({
            "type": "status", "platform": "linkedin_posts",
            "message": f"🔍 Post dari {author} mengandung sinyal lowongan!",
        })

        # ── Step 7: Extract job title FIRST, then check position match ──
        # Important: we extract the actual job title from the post BEFORE position
        # matching, because matching against the full card text causes false positives
        # (e.g. a "Brand Manager" post mentioning "purchasing" in context passes
        # when checked against full text, but correctly fails when checked against
        # the extracted title "Brand Manager").
        extracted_title = self._extract_job_title(card_text, position)
        extracted_title_lower = _normalize(extracted_title)
        position_lower = _normalize(position)

        # Check position match against the EXTRACTED title (not full card text)
        title_matches = position_matches(extracted_title, position)
        if not title_matches:
            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": f"⏭️ Post dari {author}: posisi '{extracted_title}' tidak cocok dengan target '{position}'",
            })
            _blog(f"  Card [{index}]: position mismatch — extracted='{extracted_title}' vs target='{position}'")
            return False

        # Guard: if extracted_title is just the fallback (== target position),
        # the title extraction didn't find a real matching title. Extract a
        # "raw" title WITHOUT position filtering — if the real job title is
        # something like "Brand Manager", this will catch it and skip the post.
        if extracted_title.lower() == position.lower():
            raw_title = self._extract_job_title(card_text, position, filter_by_position=False)
            if raw_title and not position_matches(raw_title, position):
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"⏭️ Post dari {author}: posisi sebenarnya '{raw_title}', bukan '{position}'",
                })
                _blog(f"  Card [{index}]: raw title '{raw_title}' doesn't match target '{position}'")
                return False
            if not position_matches(card_text, position):
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"⏭️ Post dari {author}: posisi '{extracted_title}' (fallback) tidak dikonfirmasi di post",
                })
                _blog(f"  Card [{index}]: fallback title not confirmed in card text")
                return False

        # ── Step 7b: Check location match ──
        # When user searches "purchasing bekasi", "bekasi" is the target location.
        # Posts about jobs in other cities (e.g. Sidoarjo) must be skipped.
        if location:
            job_location = self._extract_job_location(card_text, parsed)
            if job_location and not matches_location(job_location, location):
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"⏭️ Post dari {author}: lokasi kerja '{job_location}' tidak cocok dengan target '{location}'",
                })
                _blog(f"  Card [{index}]: location mismatch — job_loc='{job_location}' vs target='{location}'")
                return False

        # ── Step 8: Extract company name early for duplicate & email checks ──
        company = self._extract_company_name(card_text, parsed, author)
        if not company:
            _blog(f"  Card [{index}]: WARNING — company name not found in post")
            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": f"⚠️ Post dari {author}: nama perusahaan tidak ditemukan di post",
            })

        # ── Step 8b: Check duplicate in database (company + position) ──
        is_dup = await self._check_duplicate_in_db(card_text, position, company)
        if is_dup:
            self._consecutive_duplicates += 1
            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": f"⏭️ Post dari {author}: posisi sudah pernah dilamar ({self._consecutive_duplicates}/{self._MAX_DUPLICATES})",
            })
            await self.on_apply(
                "linkedin_posts", self._extract_job_title(card_text, position),
                author, parsed.post_url or "", position, location,
                "skipped", "Sudah pernah dilamar",
            )
            _blog(f"  Card [{index}]: already applied, consecutive={self._consecutive_duplicates}")
            return False

        # ── Step 9: Extract job links ──
        job_links = parsed.links
        application_url = parsed.post_url

        external_links = []
        for link in job_links:
            low = link.lower()
            if "linkedin.com" in low:
                continue
            if any(term in low for term in (
                "job", "career", "apply", "recruit", "forms", "docs.google",
                "bit.ly", "s.id", "linktr", "gr8people", "lever.co", "greenhouse",
                "workable", "bamboo", "smartrecruiters", "jobvite", "icims",
                "paycomonline", "successfactors", "oracle", "workday",
                "taleo", "kenexa", "brassring", "applicantpro", "hiremojo",
                "breezy", "recruitee", "pitchme", "wilogo", "glints",
                "karir.com", "jobstreet", "kalibrr", "mekerja", "tokopedia",
                "shopee", "gojek", "traveloka",
            )):
                external_links.append(link)

        emails = extract_emails("\n".join([card_text, parsed.text, "\n".join(parsed.emails)]))
        if emails:
            _blog(f"  Card [{index}]: found emails: {emails}")

        # ── Step 10: PRIORITY — If email found, send application email ──
        if emails:
            original_email = emails[0]
            # Use the extracted title from Step 7 — NOT re-extracting to avoid fallback to target position
            job_title = extracted_title
            email_job_title = _email_position_title(job_title, position)
            # company was already extracted in Step 8
            if email_config:
                testing_mode = bool(email_config.get("testing_email_mode"))
                target_email = email_config["sender_email"] if testing_mode else original_email
                mode_label = "TEST " if testing_mode else ""
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"📧 {mode_label}Mengirim email ke {target_email} untuk posisi {email_job_title}...",
                })
                success, err, email_meta = await self._send_application_email(
                    email_config, target_email, original_email, job_title, company,
                    position, location, cv_path, cv_text, cover_template, card_text,
                )
                if success:
                    if testing_mode:
                        self._test_emails_sent += 1
                    message = (
                        f"✅ TEST email berhasil dikirim ke {target_email} (asli: {original_email})! ({self._test_emails_sent}/{self._MAX_TEST_EMAILS})"
                        if testing_mode else f"✅ Email berhasil dikirim ke {target_email}!"
                    )
                    self.emit({
                        "type": "status", "platform": "linkedin_posts",
                        "message": message,
                    })
                    question_answers = json.dumps({
                        "Email Asli": original_email,
                        "Email Dikirim Ke": target_email,
                        "Subject Email": email_meta.get("subject") or "-",
                    }, ensure_ascii=False)
                    await self.on_apply(
                        "linkedin_posts", email_meta.get("job_title") or email_job_title, company or "",
                        parsed.post_url or "", position, location,
                        "found" if testing_mode else "applied",
                        f"Test email terkirim ke {target_email} (asli: {original_email})" if testing_mode else f"Email terkirim ke {target_email}",
                        job_location=location,
                        salary=expected_salary or None,
                        question_answers=question_answers,
                    )
                    return True
                else:
                    _blog(f"  Card [{index}]: email send failed: {err}")
                    self.emit({
                        "type": "error", "platform": "linkedin_posts",
                        "message": f"❌ Gagal kirim email: {str(err)[:100]}",
                    })
            else:
                _blog(f"  Card [{index}]: email not configured")
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"⚠️ Email belum dikonfigurasi di Settings. Kirim manual ke: {original_email}",
                })

        # ── Step 11: Try external links ──
        if not external_links and application_url:
            external_links.append(application_url)

        if not external_links and not emails:
            # Try to find links in the card HTML directly
            try:
                all_links = await page.evaluate(
                    """el => {
                        return Array.from(el.querySelectorAll('a[href]'))
                            .map(a => a.href)
                            .filter(h => h && h.startsWith('http'));
                    }""",
                    card,
                )
                for link in (all_links or []):
                    low = link.lower()
                    if "linkedin.com" in low and "post" not in low:
                        continue
                    if any(term in low for term in (
                        "job", "career", "apply", "recruit", "forms", "docs.google",
                        "bit.ly", "s.id", "lever.co", "greenhouse", "workable",
                        "glints", "karir.com", "jobstreet", "kalibrr",
                    )):
                        external_links.append(link)
            except Exception:
                pass

        if not external_links and not emails:
            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": f"📋 Post dari {author} tentang lowongan, tapi tidak ada link atau email apply",
            })
            _blog(f"  Card [{index}]: job-related but no links/emails found")
            return False

        if external_links:
            _blog(f"  Card [{index}]: found {len(external_links)} external links: {external_links[:3]}")

        # ── Step 12: Try to apply to each external link ──
        for link in external_links[:3]:
            if self.should_stop():
                raise asyncio.CancelledError()

            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": f"🔗 Membuka link: {link[:100]}",
            })

            try:
                await self._open_and_apply_link(
                    page, link, card_text, position, location,
                    cv_path, cv_text, cover_template, author,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _blog(f"Error opening link {link[:60]}: {e}")
                self.emit({
                    "type": "error", "platform": "linkedin_posts",
                    "message": f"Error membuka link: {str(e)[:100]}",
                })

        return True

    # ──────────────────────────────────────────────────────────────────────
    # Check if job was already applied (via database)
    # ──────────────────────────────────────────────────────────────────────

    async def _check_duplicate_in_db(self, card_text: str, position: str, company: str = "") -> bool:
        """Check if this post/job was already applied via database.
        Matches BOTH company AND position to avoid false positives."""
        try:
            from database import get_db
            db = get_db()

            rows = db.execute(
                """SELECT job_title, company FROM apply_logs
                   WHERE status IN ('applied', 'found') AND platform IN ('linkedin_posts', 'linkedin')
                   ORDER BY id DESC LIMIT 200"""
            ).fetchall()
            db.close()

            if not rows:
                return False

            card_norm = _norm(card_text)
            company_norm = _norm(company)

            for row in rows:
                applied_title = _norm(row["job_title"] or "")
                applied_company = _norm(row["company"] or "")
                if not applied_title:
                    continue

                # --- Company match check ---
                company_match = False
                if company_norm and applied_company and len(applied_company) >= 3:
                    # Exact or substring match on company name
                    if company_norm == applied_company:
                        company_match = True
                    elif applied_company in company_norm or company_norm in applied_company:
                        company_match = True
                    else:
                        # Word-level overlap for company names
                        co_words_applied = {w for w in applied_company.split() if len(w) >= 3}
                        co_words_card = {w for w in company_norm.split() if len(w) >= 3}
                        if co_words_applied and co_words_card:
                            overlap = len(co_words_applied & co_words_card)
                            if overlap >= min(len(co_words_applied), len(co_words_card)) * 0.6:
                                company_match = True

                # Fallback: if extracted company is empty, match company in card text
                if not company_match and not company_norm and applied_company and len(applied_company) >= 3:
                    if applied_company in card_norm:
                        company_match = True

                if not company_match:
                    continue

                # --- Position match check ---
                title_words = [w for w in applied_title.split() if len(w) >= 4]
                if not title_words:
                    continue
                matching_words = sum(1 for w in title_words if w in card_norm)
                if title_words and (matching_words / len(title_words)) >= 0.5:
                    return True

                # Also check if the post text contains the previously applied job title
                if applied_title and applied_title in card_norm:
                    return True

            return False
        except Exception:
            return False

    def _extract_job_title(self, card_text: str, position: str, filter_by_position: bool = True) -> str:
        """Extract job title from card text, fallback to position.

        When filter_by_position=True (default), only returns titles that match
        the target position. When False, returns the first valid title found
        regardless of position — useful for detecting non-matching roles like
        "Brand Manager" when target is "purchasing".
        """
        text = _clean_one_line(card_text)
        # When not filtering by position, pass empty string to _clean_job_title_candidate
        pos = position if filter_by_position else ""
        for pattern in (
            r"\b(?:we'?re|we are|kami|sedang)?\s*(?:hiring|open(?:ing)?|lowongan|vacancy)\s*[:\-–—]\s*([A-Z][A-Za-z0-9/&.' -]{3,80})",
            r"\b(?:position|posisi|role)\s*[:\-–—]\s*([A-Z][A-Za-z0-9/&.' -]{3,80})",
            r"\b(?:dibutuhkan|mencari|looking for)\s+([A-Z][A-Za-z0-9/&.' -]{3,80})",
        ):
            for match in re.finditer(pattern, text, flags=re.I):
                candidate = _clean_job_title_candidate(match.group(1), pos)
                if candidate:
                    return candidate

        position_words = [re.escape(w) for w in re.findall(r"[A-Za-z0-9]+", position or "") if len(w) >= 4]
        if position_words and filter_by_position:
            around_position = re.search(
                rf"\b({'|'.join(position_words)})\b(?:\s+[A-Z][A-Za-z/&.-]+){{0,4}}",
                text,
                flags=re.I,
            )
            if around_position:
                candidate = _clean_job_title_candidate(around_position.group(0), pos)
                if candidate:
                    return candidate

        lines = card_text.split("\n")
        for line in lines[:5]:
            candidate = _clean_job_title_candidate(line.strip(), pos)
            if candidate:
                return candidate
        # Only fall back to target position when filtering by position
        if filter_by_position:
            return _clean_one_line(position).title() or "Job Application"
        return ""

    def _extract_job_location(self, card_text: str, parsed: ParsedLinkedInPost) -> str:
        """Extract the actual job location from a LinkedIn post text.

        Only returns a location when we are CONFIDENT it is a real city name.
        Returns empty string if no reliable location found (post will be allowed through).

        Important: We deliberately do NOT use generic patterns like 'di X' or 'in X'
        because they match noise words inside other words (e.g. 'requirement' → 'ment').
        """
        # Known Indonesian city names — the ONLY reliable source of location info
        KNOWN_CITIES = (
            "Jakarta", "Bekasi", "Cikarang", "Tangerang", "Depok", "Bogor",
            "Bandung", "Surabaya", "Sidoarjo", "Gresik", "Lamongan",
            "Semarang", "Yogyakarta", "Sleman", "Bantul",
            "Medan", "Karawang", "Purwakarta", "Subang", "Indramayu",
            "Malang", "Palembang", "Manado", "Makassar",
            "Balikpapan", "Pontianak", "Banjarmasin", "Samarinda",
            "Batam", "Padang", "Lampung", "Cilegon", "Serang",
            "Cirebon", "Majalaya", "Solo", "Mojokerto", "Pasuruan",
            "Probolinggo", "Lumajang", "Jember", "Banyuwangi",
            "Kediri", "Blitar", "Madiun", "Tasikmalaya", "Ciamis",
            "Garut", "Sumedang", "Cianjur", "Sukabumi",
        )
        cities_lower = {c.lower(): c for c in KNOWN_CITIES}

        text = _clean_one_line(parsed.text or "")
        if not text or len(text) < 40:
            text = _clean_one_line(card_text)
        text_lower = text.lower()

        # Pattern 1: Explicit location labels followed by a known city
        # e.g. "Penempatan: Bekasi", "Location: Jakarta", "Lokasi: Surabaya"
        for label_pattern in (
            r"\b(?:location|lokasi|penempatan|placed?|based\s+in|work\s+location|\*\s*area)\s*[:\-–—\*]?\s*",
        ):
            match = re.search(label_pattern + r"([A-Za-z][A-Za-z\s,.-]{1,50})", text, re.I)
            if match:
                after_label = match.group(1).strip()
                # Check if any known city appears in the text after the label
                for city_lower, city_title in cities_lower.items():
                    if city_lower in after_label.lower():
                        return city_title

        # Pattern 2: Location in parentheses — e.g. "Purchasing Staff (Bekasi)"
        paren_match = re.search(r"\(([A-Za-z\s]{2,40})\)", text)
        if paren_match:
            inside = paren_match.group(1).strip()
            inside_lower = inside.lower()
            for city_lower, city_title in cities_lower.items():
                if city_lower in inside_lower:
                    return city_title

        # Pattern 3: Scan for any known city name in the text (word boundary match)
        for city_lower, city_title in cities_lower.items():
            if re.search(r"\b" + re.escape(city_lower) + r"\b", text_lower):
                return city_title

        return ""

    def _extract_company_name(self, card_text: str, parsed: ParsedLinkedInPost, author: str = "") -> str:
        """Conservative company extraction for email placeholder rendering.

        Only returns names that look like real company names. Returns empty
        string when uncertain — empty is better than a wrong name in emails.
        """
        candidates = []
        text = _clean_one_line(parsed.text or "")
        if not text or len(text) < 40:
            text = _clean_one_line(card_text)

        # Known location words that should NOT be extracted as company names
        LOCATION_WORDS = {
            "jakarta", "bekasi", "cikarang", "tangerang", "depok", "bogor",
            "bandung", "surabaya", "sidoarjo", "gresik", "lamongan",
            "semarang", "yogyakarta", "medan", "karawang", "malang",
            "indonesia", "remote", "hybrid", "onsite", "wfh",
        }

        for pattern in (
            # PT pattern: most reliable
            r"\b(PT\.?\s+[A-Z][A-Za-z0-9&.' -]{2,70})\b",
            # "join our team at Company" pattern
            r"\bjoin\s+(?:our\s+team\s+at\s+)?([A-Z][A-Za-z0-9&.' -]{2,50})\b",
            # "Company is/are looking/hiring" pattern
            r"\b([A-Z][A-Za-z0-9&.' -]{2,50})\s+(?:is|are)\s+(?:looking|hiring|opening)\b",
            # Pipe/dash separator pattern: "— Company Indonesia" or "| Company Group"
            r"[-–|]\s*([A-Z][A-Za-z0-9&.' -]{2,50}\s+(?:Indonesia|Group|Consulting|Corp))\b",
        ):
            for match in re.finditer(pattern, text):
                raw = match.group(1).strip()
                raw_lower = raw.lower()
                # Skip if it's a known location word
                if raw_lower in LOCATION_WORDS:
                    continue
                # Skip if it starts with a location word
                if any(raw_lower.startswith(loc) for loc in LOCATION_WORDS):
                    continue
                # Skip if it contains sentence-like words
                if re.search(r"\b(who|here|what|why|when|where|how|with|for|the|and|this|that|from)\b", raw_lower):
                    continue
                # Skip if it's too long (likely a sentence fragment)
                if len(raw) > 50:
                    continue
                context = text[max(0, match.start() - 90):match.end() + 60]
                if re.search(r"\b(formerly|recruitment consultant|consultant at|talent acquisition|headhunter|career advisor)\b", context, re.I):
                    continue
                candidates.append(raw)

        for candidate in candidates:
            cleaned = _clean_company_candidate(candidate)
            if cleaned:
                return cleaned

        return ""

    def _extract_subject_hint(self, post_text: str, job_title: str, candidate_name: str, location: str = "") -> str:
        text = _clean_one_line(post_text)
        patterns = (
            r"(?:with\s+)?(?:subject|subjek)(?:\s+(?:email|line))?\s*[:\-]?\s*\[([^\]]{2,80})\]",
            r"(?:with\s+)?(?:subject|subjek)(?:\s+(?:email|line))?\s*[:\-]?\s*[\"']([^\"']{2,80})[\"']",
            r"(?:with\s+)?(?:subject|subjek)(?:\s+(?:email|line))?\s*[:\-]?\s*((?:CV|Resume|Lamaran)[A-Za-z0-9_/|&+.-]{1,80})",
            r"(?:with\s+)?(?:subject|subjek)(?:\s+(?:email|line))?\s*[:\-]\s*([A-Za-z0-9][A-Za-z0-9 _/|&+.-]{1,80})(?:\s+(?:or|atau|dan|and)\b|[.;]|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.I)
            if not match:
                continue
            hint = _clean_one_line(match.group(1))
            hint = re.sub(r"position[_\s-]*name|job[_\s-]*title|nama[_\s-]*posisi|posisi kerja|(?<![A-Za-z0-9])position(?![A-Za-z0-9])|(?<![A-Za-z0-9])posisi(?![A-Za-z0-9])", job_title, hint, flags=re.I)
            hint = re.sub(r"location[_\s-]*name|job[_\s-]*location|lokasi[_\s-]*kerja|(?<![A-Za-z0-9])location(?![A-Za-z0-9])|(?<![A-Za-z0-9])lokasi(?![A-Za-z0-9])", _clean_one_line(location), hint, flags=re.I)
            hint = re.sub(r"your[_\s-]*name|full[_\s-]*name|nama[_\s-]*lengkap|candidate[_\s-]*name|applicant[_\s-]*name|(?<![A-Za-z0-9])nama(?![A-Za-z0-9])", candidate_name, hint, flags=re.I)
            hint = _clean_one_line(hint)
            if 2 <= len(hint) <= 100:
                return hint
        return ""

    def _build_email_subject(self, job_title: str, candidate_name: str, post_text: str, location: str = "") -> str:
        role = _clean_one_line(job_title) or "Job Application"
        full_name = _clean_one_line(candidate_name) or "Applicant"
        subject = self._extract_subject_hint(post_text, role, full_name, location)
        if not subject:
            subject = f"{role}_{full_name}"
        return subject[:180]

    # ──────────────────────────────────────────────────────────────────────
    # Expand card text — click all "more" buttons
    # Uses text-based matching since CSS classes are obfuscated
    # ──────────────────────────────────────────────────────────────────────

    async def _expand_card_text(self, page, card):
        """
        Click all "…more" / "see more" buttons inside a post card.
        Uses text-based matching since LinkedIn uses obfuscated CSS classes.
        """
        # Click via JS — find buttons by text content within the card element
        try:
            clicked = await page.evaluate(
                """card => {
                    const btns = Array.from(card.querySelectorAll(
                        'button, span[role="button"], a[role="button"]'
                    ));
                    let hitCount = 0;
                    for (const b of btns) {
                        const t = (b.textContent || '').trim();
                        const label = (b.getAttribute('aria-label') || '').toLowerCase();
                        if (
                            t === '… more' || t === '...more' || t === '…more' ||
                            t === '... more' || t === 'see more' ||
                            t === 'selengkapnya' || t === 'lihat lebih banyak' ||
                            label.includes('see more') || label.includes('lihat lebih') ||
                            label.includes('show more')
                        ) {
                            b.scrollIntoView({ block: 'center' });
                            b.click();
                            hitCount++;
                        }
                    }
                    return hitCount;
                }""",
                card,
            )
            if clicked:
                _blog(f"  Expanded: clicked {clicked} more button(s)")
                await asyncio.sleep(0.6)

                # Retry once more — LinkedIn sometimes needs two clicks
                await page.evaluate(
                    """card => {
                        const btns = Array.from(card.querySelectorAll(
                            'button, span[role="button"], a[role="button"]'
                        ));
                        for (const b of btns) {
                            const t = (b.textContent || '').trim();
                            if (t === '… more' || t === '...more' || t === '…more') {
                                b.click();
                            }
                        }
                    }""",
                    card,
                )
                await asyncio.sleep(0.4)
        except Exception as e:
            _blog(f"  Expand error: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Open external link and try to apply
    # ──────────────────────────────────────────────────────────────────────

    async def _open_and_apply_link(
        self, page, link, post_text, position, location,
        cv_path, cv_text, cover_template, author,
    ):
        """Open an external job link and try to apply."""
        try:
            new_page = await page.context.new_page()
            try:
                await new_page.goto(link, timeout=30000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"⏰ Timeout membuka link: {link[:80]}",
                })
                await new_page.close()
                return

            await self._delay(2, 3)

            current_url = new_page.url
            if "login" in current_url or "authwall" in current_url:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"🔒 Link memerlukan login: {link[:80]}",
                })
                await new_page.close()
                return

            # Google Form
            if "docs.google.com/forms" in current_url or "forms.gle" in current_url:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"📝 Google Form ditemukan: {link[:80]}",
                })
                await self._handle_google_form(new_page, cv_text, position)
                await new_page.close()
                return

            # ATS platforms
            if any(platform in current_url for platform in (
                "lever.co", "greenhouse.io", "workable.com", "smartrecruiters.com",
                "bamboohr.com", "jobvite.com", "icims.com", "paycomonline.net",
                "successfactors.com", "workday.com", "taleo.net",
            )):
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"🏢 ATS platform detected: {link[:80]}",
                })
                await self._try_generic_form_fill(new_page, cv_path, cv_text, position, cover_template)
                await new_page.close()
                return

            # Generic form
            has_form = await new_page.query_selector("form")
            if has_form:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"📋 Form ditemukan di: {link[:80]}",
                })
                await self._try_generic_form_fill(new_page, cv_path, cv_text, position, cover_template)
            else:
                self.emit({
                    "type": "status", "platform": "linkedin_posts",
                    "message": f"📄 Halaman tanpa form: {link[:80]}",
                })

            await new_page.close()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            _blog(f"Error in _open_and_apply_link: {e}")
            self.emit({
                "type": "error", "platform": "linkedin_posts",
                "message": f"Error apply: {str(e)[:100]}",
            })

    # ──────────────────────────────────────────────────────────────────────
    # Google Form handler
    # ──────────────────────────────────────────────────────────────────────

    async def _handle_google_form(self, page, cv_text, position):
        """Try to fill Google Form fields."""
        try:
            inputs = await page.query_selector_all(
                "input[type='text'], input[type='email'], input[type='tel'], textarea"
            )
            for inp in inputs:
                try:
                    current = await inp.input_value()
                    if current:
                        continue
                    label = (
                        await inp.get_attribute("aria-label") or
                        await inp.get_attribute("placeholder") or
                        await inp.get_attribute("name") or ""
                    ).lower()

                    value = ""
                    if "name" in label or "nama" in label:
                        value = ""
                    elif "email" in label or "surel" in label:
                        value = ""
                    elif "phone" in label or "telepon" in label or "hp" in label or "wa" in label:
                        value = ""
                    elif "position" in label or "posisi" in label or "role" in label:
                        value = position
                    elif "cv" in label or "resume" in label:
                        self.emit({
                            "type": "status", "platform": "linkedin_posts",
                            "message": "⚠️ Form memerlukan upload CV manual",
                        })
                    elif "link" in label or "portfolio" in label:
                        value = ""
                    else:
                        ft = "number" if any(w in label for w in ("salary", "gaji", "year", "tahun")) else "text"
                        value = await answer_application_question(
                            self.user_id, "linkedin_posts", label, ft, cv_text, position, None,
                        )

                    if value:
                        await inp.fill(value)
                        await self._delay(0.1, 0.2)
                except Exception:
                    continue

            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": "✅ Form fields filled (check & submit manually)",
            })
        except Exception as e:
            _blog(f"Google Form error: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Generic form filler
    # ──────────────────────────────────────────────────────────────────────

    async def _try_generic_form_fill(self, page, cv_path, cv_text, position, cover_template):
        """Try to fill generic application forms on external sites."""
        try:
            inputs = await page.query_selector_all(
                "input[type='text'], input[type='email'], input[type='tel'], "
                "input[type='number'], input:not([type]), textarea"
            )
            for inp in inputs:
                try:
                    current = await inp.input_value()
                    if current:
                        continue
                    label = (
                        await inp.get_attribute("aria-label") or
                        await inp.get_attribute("placeholder") or
                        await inp.get_attribute("name") or
                        await inp.get_attribute("id") or ""
                    ).lower()

                    value = ""
                    if any(w in label for w in ("email", "surel")):
                        value = ""
                    elif any(w in label for w in ("phone", "telepon", "hp", "wa", "mobile")):
                        value = ""
                    elif any(w in label for w in ("position", "posisi", "role", "job title")):
                        value = position
                    elif "salary" in label or "gaji" in label:
                        value = ""
                    elif any(w in label for w in ("cover letter", "surat lamaran")):
                        if cover_template:
                            value = render_cover_letter_template(cover_template, position, "")
                    else:
                        ft = "number" if any(w in label for w in ("salary", "gaji", "year", "tahun")) else "text"
                        value = await answer_application_question(
                            self.user_id, "linkedin_posts", label, ft, cv_text, position, None,
                        )

                    if value:
                        await inp.fill(value)
                        await self._delay(0.1, 0.2)
                except Exception:
                    continue

            file_inputs = await page.query_selector_all("input[type='file']")
            for fi in file_inputs:
                try:
                    accept = (await fi.get_attribute("accept") or "").lower()
                    if "pdf" in accept or not accept:
                        await fi.set_input_files(prepare_upload_file(cv_path))
                        self.emit({
                            "type": "status", "platform": "linkedin_posts",
                            "message": "📎 CV diunggah",
                        })
                        await self._delay(0.5, 1)
                        break
                except Exception:
                    continue

            self.emit({
                "type": "status", "platform": "linkedin_posts",
                "message": "⚠️ Form diisi otomatis — silakan review & submit manual",
            })
        except Exception as e:
            _blog(f"Generic form error: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # Send application email with cover letter + CV
    # ──────────────────────────────────────────────────────────────────────

    async def _send_application_email(
        self, email_config, target_email, original_email, job_title, company,
        position, location, cv_path, cv_text, cover_template, post_text="",
    ):
        """Send application email with cover letter and CV attachment."""
        try:
            testing_mode = bool(email_config.get("testing_email_mode"))
            sender_name = _sender_display_name(email_config["sender_email"])
            candidate_name = _candidate_full_name(cv_text, email_config.get("user_name") or sender_name)
            email_job_title = _email_position_title(job_title, position)

            # Generate cover letter
            cover_letter = ""
            if cover_template:
                cover_letter = render_cover_letter_template(
                    cover_template, email_job_title, company
                )
            cover_letter = (cover_letter or "").strip()
            if not cover_letter:
                return False, "Cover letter target kosong. Isi cover letter di UI agar bot tidak membuat email dari awal.", {}

            # Build subject line
            subject = self._build_email_subject(email_job_title, candidate_name, post_text, location)

            # Build email body
            body = cover_letter

            # ── Gemini email validation: check for duplicate company name, correctness ──
            try:
                validated = await validate_and_fix_email(
                    subject, body, company, email_job_title, candidate_name,
                )
                if validated:
                    issues = validated.get("issues") or []
                    if issues:
                        _blog(f"  Gemini email fix: {'; '.join(issues)}")
                        self.emit({
                            "type": "status", "platform": "linkedin_posts",
                            "message": f"🔧 Email dikoreksi oleh AI: {'; '.join(issues[:2])}",
                        })
                    subject = validated.get("subject") or subject
                    body = validated.get("body") or body
            except Exception as ve:
                _blog(f"  Gemini email validation failed (continuing with original): {ve}")

            # Send email (with CV attachment)
            attachment = None
            if cv_path:
                try:
                    resolved_cv_path = prepare_upload_file(cv_path)
                    attachment = resolved_cv_path if os.path.exists(resolved_cv_path) else None
                except Exception:
                    attachment = cv_path if os.path.exists(cv_path) else None
            success, err = send_email(
                email_config["smtp_host"],
                email_config["smtp_port"],
                email_config["sender_email"],
                email_config["app_password"],
                target_email,
                subject,
                body,
                attachment,
            )
            return success, err, {
                "subject": subject,
                "job_title": email_job_title,
                "company": company or "",
                "candidate_name": candidate_name,
                "target_email": target_email,
                "original_email": original_email,
                "testing_mode": testing_mode,
            }
        except Exception as e:
            _blog(f"  _send_application_email error: {e}")
            return False, str(e), {}

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    async def _scroll_feed(self, page):
        """Scroll the feed down to load more posts."""
        try:
            await page.mouse.wheel(0, 2000)
        except Exception:
            pass

    async def _dismiss_overlay(self, page):
        """Dismiss any login overlay or popup."""
        try:
            close_btn = await page.query_selector(
                ".modal__overlay button[aria-label='Dismiss'], "
                ".modal__overlay .artdeco-modal__dismiss, "
                ".modal__overlay svg[data-test-icon='close'], "
                "[data-test-modal-close-btn]"
            )
            if close_btn and await close_btn.is_visible():
                await close_btn.click(force=True, timeout=3000)
                await self._delay(0.5, 1)
        except Exception:
            pass

    async def _safe_text(self, el_or_page, selector=None) -> str:
        """Safely get text from an element."""
        try:
            el = await el_or_page.query_selector(selector) if selector else el_or_page
            return (await el.inner_text()).strip() if el else ""
        except Exception:
            return ""

    async def _delay(self, mn=1.0, mx=3.0):
        """Random delay with stop check."""
        if self.should_stop():
            try:
                if self._browser:
                    await self._browser.close()
            except Exception:
                pass
            raise asyncio.CancelledError()
        await asyncio.sleep(random.uniform(mn, mx))
