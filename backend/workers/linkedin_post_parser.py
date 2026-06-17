"""
LinkedIn Post Parser — menggunakan Scrapling (Adaptor) sebagai parser utama.

Scrapling Adaptor dipakai untuk:
- extract author, description, post text via CSS selector yang akurat
- get_all_text() yang bersih (no HTML noise, handle entities otomatis)
- extract email via regex pada clean text
- extract links via css('a[href]')

Fallback ke regex-based strip_tags jika Scrapling tidak terinstall.
"""
import hashlib
import html
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin


LINKEDIN_BASE = "https://www.linkedin.com"

# Selector CSS LinkedIn untuk berbagai versi UI
ACTOR_NAME_SELECTORS = (
    ".update-components-actor__name [aria-hidden='true']",
    ".update-components-actor__name",
    ".feed-shared-actor__name [aria-hidden='true']",
    ".feed-shared-actor__name",
    ".entity-result__title-text a",
    ".entity-result__title-text",
    "span[dir='ltr']",
)

ACTOR_DESC_SELECTORS = (
    ".update-components-actor__description [aria-hidden='true']",
    ".update-components-actor__description",
    ".feed-shared-actor__description",
    ".update-components-actor__meta-link",
)

POST_TEXT_SELECTORS = (
    ".update-components-text",
    ".feed-shared-update-v2__description",
    ".feed-shared-text",
    ".update-components-text__text-view",
    "[data-test-id='main-feed-activity-card__commentary']",
)

POST_URL_PATTERNS = ("activity-", "/posts/", "/feed/update/", "urn:li:activity")

UI_NOISE_PATTERNS = (
    r"\bLike\b", r"\bComment\b", r"\bRepost\b", r"\bShare\b",
    r"\bFollow\b", r"\bConnect\b", r"\bActivate to view larger image\b",
    r"\bView .* profile\b", r"\bSee more\b", r"\bShow more\b", r"\bShow less\b",
    r"\bEdited\b", r"\bPromoted\b", r"\bReport this post\b",
    r"\bSend\b",
)

# Regex fallback untuk author jika Scrapling tidak tersedia
AUTHOR_PATTERNS_REGEX = (
    r'<[^>]+class="[^"]*(?:update-components-actor__name|feed-shared-actor__name|entity-result__title-text)[^"]*"[^>]*>(?P<value>.*?)</[^>]+>',
    r'<span[^>]+dir="ltr"[^>]*>(?P<value>.*?)</span>',
)


@dataclass
class ParsedLinkedInPost:
    text: str = ""
    author: str = ""
    author_description: str = ""      # jabatan/perusahaan dari actor description
    post_url: str = ""
    links: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    fingerprint: str = ""
    parser: str = "fallback"


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_tags(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", value or "", flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|span|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return clean_text(text)


def strip_ui_noise(value: str) -> str:
    text = value or ""
    for pattern in UI_NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\b\d+\s*(?:reactions?|comments?|reposts?)\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


COMMON_TLDS = (
    "academy", "accountant", "agency", "app", "asia", "biz", "careers", "co", "co.id",
    "com", "com.au", "com.sg", "company", "consulting", "edu", "go.id", "gov", "hr",
    "id", "in", "info", "io", "jobs", "net", "org", "recruitment", "sg", "tech",
)


def normalize_email_candidate(value: str) -> str:
    email_addr = html.unescape(value or "").strip().strip(".,;:)]}>\"'")
    email_addr = re.sub(r"^(?:mailto:)", "", email_addr, flags=re.I)
    email_addr = re.sub(r"\s+", "", email_addr)
    if "@" not in email_addr:
        return ""

    local, domain = email_addr.rsplit("@", 1)
    local = local.strip("._-+")
    domain = domain.lower().strip(".-_")
    if not local or not domain or "." not in domain:
        return ""

    best = ""
    for tld in COMMON_TLDS:
        marker = f".{tld}"
        idx = domain.find(marker)
        if idx == -1:
            continue
        end = idx + len(marker)
        candidate = domain[:end]
        if len(candidate) > len(best):
            best = candidate

    if best:
        domain = best
    else:
        domain = re.sub(r"[^a-z0-9.-].*$", "", domain)
        domain = re.sub(r"\d+$", "", domain)

    if domain.endswith(("linkedin.com", "email.com", "example.com")):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        return ""
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", domain):
        return ""
    return f"{local}@{domain}"


def extract_emails(value: str) -> list[str]:
    seen = set()
    out = []
    pattern = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9.-]+"
    for raw_email in re.findall(pattern, value or ""):
        email_addr = normalize_email_candidate(raw_email)
        key = email_addr.lower()
        if not email_addr or key in seen:
            continue
        seen.add(key)
        out.append(email_addr)
    return out


def extract_links(markup: str) -> list[str]:
    seen = set()
    out = []
    for raw in re.findall(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", markup or "", flags=re.I):
        href = html.unescape(raw).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        href = urljoin(LINKEDIN_BASE, href).split("?")[0]
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def choose_post_url(links: list[str], fallback: str = "") -> str:
    for href in links:
        if any(p in href for p in POST_URL_PATTERNS):
            return href
    return fallback or ""


def choose_application_url(links: list[str]) -> str:
    for href in links:
        low = href.lower()
        if "linkedin.com" in low:
            continue
        if any(term in low for term in ("job", "career", "apply", "recruit", "forms", "docs.google", "bit.ly", "s.id", "linktr")):
            return href
    return ""


def extract_author_regex(markup: str, raw_text: str = "") -> str:
    """Fallback author extraction pakai regex jika Scrapling tidak tersedia."""
    for pattern in AUTHOR_PATTERNS_REGEX:
        match = re.search(pattern, markup or "", flags=re.I | re.S)
        if not match:
            continue
        author = strip_tags(match.group("value"))
        author = re.sub(r"\s*(?:View .* profile|Follow|Connect).*", "", author, flags=re.I).strip()
        if 2 <= len(author) <= 120:
            return author
    lines = [line.strip() for line in re.split(r"\n| {2,}", raw_text or "") if line.strip()]
    for line in lines[:5]:
        line = clean_text(line)
        if 2 <= len(line) <= 80 and not re.search(r"\b(like|comment|repost|send|hiring|lowongan)\b", line, re.I):
            return line
    return ""


def _scrapling_parse(markup: str) -> tuple[str, str, str, list[str], list[str], str]:
    """
    Parse HTML post card menggunakan Scrapling Adaptor.

    Returns:
        (text, author, author_description, links, emails, parser_name)
    """
    try:
        from scrapling.parser import Adaptor  # type: ignore
    except ImportError:
        try:
            from scrapling import Adaptor  # type: ignore
        except ImportError:
            return "", "", "", [], [], ""

    try:
        page = Adaptor(markup, url=LINKEDIN_BASE)
    except Exception:
        return "", "", "", [], [], ""

    # ── Author name ───────────────────────────────────────────────────────────
    author = ""
    for sel in ACTOR_NAME_SELECTORS:
        try:
            el = page.css_first(sel)
            if el:
                candidate = clean_text(str(el.get_all_text(separator=" ", strip=True)))
                candidate = re.sub(r"\s*(?:View .* profile|Follow|Connect).*", "", candidate, flags=re.I).strip()
                if 2 <= len(candidate) <= 120:
                    author = candidate
                    break
        except Exception:
            continue

    # ── Author description (jabatan • perusahaan) ─────────────────────────────
    author_description = ""
    for sel in ACTOR_DESC_SELECTORS:
        try:
            el = page.css_first(sel)
            if el:
                candidate = clean_text(str(el.get_all_text(separator=" ", strip=True)))
                if 2 <= len(candidate) <= 200:
                    author_description = candidate
                    break
        except Exception:
            continue

    # ── Post body text (content area saja, bukan UI noise) ───────────────────
    post_text = ""
    for sel in POST_TEXT_SELECTORS:
        try:
            el = page.css_first(sel)
            if el:
                candidate = clean_text(str(el.get_all_text(separator=" ", strip=True)))
                if len(candidate) >= 30:
                    post_text = candidate
                    break
        except Exception:
            continue

    # Fallback: full page text minus UI noise
    if not post_text:
        try:
            full = str(page.get_all_text(separator=" ", strip=True))
            post_text = strip_ui_noise(full)
        except Exception:
            pass

    # ── Links via CSS a[href] ─────────────────────────────────────────────────
    links = []
    seen_links = set()
    try:
        link_els = page.css("a[href]")
        for link_el in link_els:
            try:
                href = html.unescape((link_el.attrib.get("href") or "").strip())
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue
                href = urljoin(LINKEDIN_BASE, href).split("?")[0]
                if href not in seen_links:
                    seen_links.add(href)
                    links.append(href)
            except Exception:
                continue
    except Exception:
        # Fallback ke regex jika css() gagal
        links = extract_links(markup)

    # ── Emails dari clean text ────────────────────────────────────────────────
    combined_for_email = " ".join(filter(None, [post_text, author_description, markup]))
    emails = extract_emails(combined_for_email)

    return post_text, author, author_description, links, emails, "scrapling"


def parse_linkedin_post_card(
    markup: str,
    raw_text: str = "",
    fallback_post_url: str = "",
) -> ParsedLinkedInPost:
    """
    Parse satu LinkedIn post card HTML menjadi ParsedLinkedInPost.

    Flow:
    1. Coba Scrapling Adaptor (CSS selector, get_all_text — lebih akurat)
    2. Fallback ke regex strip_tags jika Scrapling tidak tersedia
    """
    markup   = markup or ""
    raw_text = raw_text or ""

    # ── Coba Scrapling ────────────────────────────────────────────────────────
    scrapling_text, author, author_description, links, emails, parser_name = _scrapling_parse(markup)

    if scrapling_text:
        text = strip_ui_noise(scrapling_text)
    else:
        # Fallback regex
        text = strip_ui_noise(strip_tags(markup) or raw_text)
        author = extract_author_regex(markup, raw_text)
        author_description = ""
        links = extract_links(markup)
        emails = extract_emails("\n".join([text, raw_text, markup]))
        parser_name = "fallback"

    # Pastikan teks cukup panjang
    if len(text) < 40 and raw_text:
        text = strip_ui_noise(raw_text)

    # Tambahkan emails dari raw_text juga (lebih lengkap)
    for email in extract_emails(raw_text):
        if email not in emails:
            emails.append(email)

    post_url = choose_post_url(links, fallback_post_url)

    fingerprint_base = clean_text(" ".join([author, text[:500], post_url]))
    fingerprint = (
        hashlib.sha1(fingerprint_base.encode("utf-8", "ignore")).hexdigest()
        if fingerprint_base else ""
    )

    return ParsedLinkedInPost(
        text=text,
        author=author,
        author_description=author_description,
        post_url=post_url,
        links=links,
        emails=emails,
        fingerprint=fingerprint,
        parser=parser_name,
    )
