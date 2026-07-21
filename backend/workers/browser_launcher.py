"""
Helper terpusat untuk membuka browser Playwright.

- Kalau STEEL_API_KEY diisi di .env: connect ke browser remote Steel.dev
  lewat CDP. Dipakai kalau backend dijalankan di host dengan RAM kecil
  (misalnya Render free tier 512MB) yang tidak sanggup jalankan Chromium
  lokal berbarengan dengan FastAPI + scheduler.
- Kalau STEEL_API_KEY kosong: tetap launch Chromium lokal seperti sebelumnya
  (dipakai kalau backend jalan di VPS dengan RAM cukup, misalnya Oracle
  ARM Ampere 24GB).

Dipakai sebagai pengganti langsung `p.chromium.launch(...)` di
jobstreet_bot.py, linkedin_bot.py, dan linkedin_posts_bot.py.
"""

import os

STEEL_API_KEY = os.getenv("STEEL_API_KEY")

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
]


async def launch_browser(p, headless: bool = True):
    """Return objek Browser dari Playwright. Pemanggil tetap pakai
    browser.new_context(...) dan browser.close() persis seperti sebelumnya —
    tidak perlu tahu apakah browsernya lokal atau remote Steel.dev."""

    if STEEL_API_KEY:
        try:
            from steel import Steel
        except ImportError as e:
            raise RuntimeError(
                "STEEL_API_KEY diisi tapi package 'steel-sdk' belum terinstall. "
                "Jalankan: pip install steel-sdk"
            ) from e

        client = Steel(steel_api_key=STEEL_API_KEY)
        session = client.sessions.create()

        browser = await p.chromium.connect_over_cdp(
            f"{session.websocket_url}&apiKey={STEEL_API_KEY}"
        )

        # Supaya session Steel dilepas otomatis begitu code yang sudah ada
        # manggil browser.close() (tidak perlu ubah call site lain).
        original_close = browser.close

        async def _close_and_release():
            try:
                await original_close()
            finally:
                try:
                    client.sessions.release(session.id)
                except Exception:
                    pass

        browser.close = _close_and_release
        return browser

    # Default: Chromium lokal (perilaku sebelum ada Steel.dev)
    return await p.chromium.launch(headless=headless, args=LAUNCH_ARGS)
