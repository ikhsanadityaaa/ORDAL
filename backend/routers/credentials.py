import asyncio
import os
import sys
import threading

from fastapi import APIRouter, Depends, HTTPException
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from auth_utils import get_current_user
from database import get_db

router = APIRouter()

COOKIES_DIR = "cookies"
os.makedirs(COOKIES_DIR, exist_ok=True)

LOGIN_TIMEOUT_MS = int(os.getenv("LOGIN_TIMEOUT_MS", "300000"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def cookies_path(user_id: int, platform_name: str) -> str:
    return os.path.join(COOKIES_DIR, f"{user_id}_{platform_name}.json")


def save_credential_marker(user_id: int, platform_name: str, method: str):
    db = get_db()
    db.execute(
        """
        INSERT INTO user_credentials (user_id, platform, email, password)
        VALUES (?, ?, ?, '')
        ON CONFLICT(user_id, platform) DO UPDATE SET
            email = excluded.email,
            updated_at = datetime('now')
        """,
        (user_id, platform_name, method),
    )
    db.commit()
    db.close()


PLATFORM_CONFIG = {
    "linkedin": {
        "label": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "check_url": "https://www.linkedin.com/feed/",
        "cookie_urls": ["https://www.linkedin.com", "https://linkedin.com"],
        "key_cookies": ["li_at"],
        "invalid_url_parts": ["login", "authwall"],
        "logged_in_selectors": [
            "a[href*='/in/']",
            "button:has(img)",
            "[data-control-name='identity_welcome_message']",
        ],
    },
    "jobstreet": {
        "label": "JobStreet",
        "login_url": "https://id.jobstreet.com/id",
        "check_url": "https://id.jobstreet.com/id",
        "cookie_urls": [
            "https://id.jobstreet.com",
            "https://www.jobstreet.com",
            "https://seek.com",
        ],
        "key_cookies": [
            "SEEK_AU_AUTH",
            "JobseekerSessionToken",
            "id_token",
            "seekSessionToken",
        ],
        "invalid_url_parts": [],
        "logged_in_selectors": [
            "button:has(img)",
            "button[aria-label*='profile' i]",
            "button[aria-label*='account' i]",
            "[data-automation*='profile' i]",
            "[data-automation*='account' i]",
            "a[href*='/profile']",
            "a[href*='/id/profile']",
        ],
    },
}


async def _has_login_cookie(context, cfg: dict):
    cookies = await context.cookies(cfg["cookie_urls"])
    cookie_names = [c.get("name") for c in cookies]
    has_key = any(n in cookie_names for n in cfg["key_cookies"])
    return has_key, len(cookies), cookie_names


async def _has_logged_in_ui(page, cfg: dict) -> bool:
    for selector in cfg.get("logged_in_selectors", []):
        try:
            if await page.locator(selector).first.is_visible(timeout=750):
                return True
        except Exception:
            pass
    return False


async def _run_grab(platform_name: str, user_id: int):
    """Coroutine yang menjalankan Playwright. Dipanggil di event loop baru."""
    cfg = PLATFORM_CONFIG[platform_name]
    state_path = cookies_path(user_id, platform_name)

    logged_in    = False
    cookie_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-dev-shm-usage"],
        )

        context_kwargs = {"user_agent": USER_AGENT}
        if os.path.exists(state_path):
            context_kwargs["storage_state"] = state_path

        context = await browser.new_context(**context_kwargs)
        page    = await context.new_page()
        await page.goto(cfg["login_url"], timeout=60000)

        deadline = asyncio.get_running_loop().time() + (LOGIN_TIMEOUT_MS / 1000)
        while asyncio.get_running_loop().time() < deadline:
            await page.wait_for_timeout(2000)
            has_key, cookie_count, _ = await _has_login_cookie(context, cfg)
            is_invalid = any(p in page.url.lower() for p in cfg["invalid_url_parts"])
            has_ui     = await _has_logged_in_ui(page, cfg)

            if not is_invalid and (has_key or has_ui):
                logged_in = True
                break

            if has_key:
                try:
                    await page.goto(cfg["check_url"], timeout=30000)
                except PlaywrightTimeout:
                    pass

        if logged_in:
            await context.storage_state(path=state_path)

        await browser.close()

    return logged_in, cookie_count


def _run_grab_in_new_loop(platform_name: str, user_id: int):
    """
    Windows fix: jalankan Playwright di thread baru dengan event loop
    ProactorEventLoop supaya subprocess bisa dibuat.
    """
    result = {"logged_in": False, "cookie_count": 0, "error": None}

    def thread_target():
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            logged_in, cookie_count = loop.run_until_complete(
                _run_grab(platform_name, user_id)
            )
            result["logged_in"]    = logged_in
            result["cookie_count"] = cookie_count
        except Exception as e:
            result["error"] = str(e)
        finally:
            loop.close()

    t = threading.Thread(target=thread_target)
    t.start()
    t.join(timeout=LOGIN_TIMEOUT_MS / 1000 + 30)

    return result


@router.post("/grab/{platform_name}")
async def grab_cookies(platform_name: str, user=Depends(get_current_user)):
    if platform_name not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Platform tidak valid")

    cfg = PLATFORM_CONFIG[platform_name]

    # Jalankan di thread terpisah dengan ProactorEventLoop (Windows fix)
    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, _run_grab_in_new_loop, platform_name, user["id"]
    )

    if result["error"]:
        raise HTTPException(
            status_code=500,
            detail=f"Gagal membuka browser login: {result['error']}",
        )

    if not result["logged_in"]:
        return {
            "success":   False,
            "logged_in": False,
            "login_url": cfg["login_url"],
            "message": (
                f"Session {cfg['label']} belum terdeteksi. "
                "Login di browser yang terbuka, lalu coba lagi."
            ),
        }

    save_credential_marker(user["id"], platform_name, "playwright_session")

    return {
        "success":   True,
        "logged_in": True,
        "message":   f"Session {cfg['label']} terdeteksi. {result['cookie_count']} cookies tersimpan.",
    }


@router.post("/assume/{platform_name}")
def assume_logged_in(platform_name: str, user=Depends(get_current_user)):
    if platform_name not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Platform tidak valid")

    save_credential_marker(user["id"], platform_name, "manual_login")
    return {
        "success":   True,
        "logged_in": True,
        "message": (
            f"{PLATFORM_CONFIG[platform_name]['label']} ditandai sudah login. "
            "Capture session tetap dibutuhkan agar ORDAL bisa membaca platform otomatis."
        ),
    }


@router.get("/status")
def check_status(user=Depends(get_current_user)):
    result = {}
    db     = get_db()
    for platform_name in PLATFORM_CONFIG:
        path = cookies_path(user["id"], platform_name)
        has_storage_state = os.path.exists(path)
        row  = db.execute(
            "SELECT email FROM user_credentials WHERE user_id = ? AND platform = ?",
            (user["id"], platform_name),
        ).fetchone()
        result[platform_name] = {
            "logged_in":   has_storage_state,
            "needs_capture": row is not None and not has_storage_state,
            "cookie_count": 1 if has_storage_state else 0,
            "method":      row["email"] if row else None,
        }
    db.close()
    return result


@router.delete("/{platform_name}")
def delete_cookies(platform_name: str, user=Depends(get_current_user)):
    if platform_name not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Platform tidak valid")

    path = cookies_path(user["id"], platform_name)
    if os.path.exists(path):
        os.remove(path)

    db = get_db()
    db.execute(
        "DELETE FROM user_credentials WHERE user_id = ? AND platform = ?",
        (user["id"], platform_name),
    )
    db.commit()
    db.close()

    return {"message": f"Session {platform_name} dihapus"}
