import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from dotenv import load_dotenv

load_dotenv()

from database import init_db, get_db, restore_persisted_files
from routers import auth, credentials, cv, targets, sessions, preferences, question_bank, telegram
from routers.email_config import router as email_router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")


def _get_cors_origins() -> list[str]:
    """
    Ambil allowed origins dari environment variable CORS_ORIGINS.
    Format: https://app.vercel.app,http://localhost:5173
    """
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["http://localhost:5173"]


app = FastAPI(title="ORDAL API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(os.path.join(UPLOADS_DIR, "cvs"), exist_ok=True)
os.makedirs(COOKIES_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.include_router(auth.router,        prefix="/api/auth",        tags=["Auth"])
app.include_router(credentials.router, prefix="/api/credentials", tags=["Credentials"])
app.include_router(cv.router,          prefix="/api/cvs",         tags=["CVs"])
app.include_router(targets.router,     prefix="/api/targets",     tags=["Targets"])
app.include_router(sessions.router,    prefix="/api/sessions",    tags=["Sessions"])
app.include_router(email_router,       prefix="/api/email",       tags=["Email"])
app.include_router(preferences.router, prefix="/api/preferences", tags=["Preferences"])
app.include_router(question_bank.router, prefix="/api/questions", tags=["Questions"])
app.include_router(telegram.router, prefix="/api/telegram", tags=["Telegram"])


@app.on_event("startup")
async def startup():
    init_db()
    restore_persisted_files()
    db = get_db()
    db.execute("UPDATE apply_sessions SET status='stopped', ended_at=datetime('now') WHERE status='running'")
    db.commit()
    db.close()
    from services.telegram_service import start_background_tasks
    start_background_tasks()
    from services.auto_apply_scheduler import start_auto_apply_scheduler
    start_auto_apply_scheduler()


@app.on_event("shutdown")
async def shutdown():
    from services.telegram_service import stop_background_tasks
    await stop_background_tasks()
    from services.auto_apply_scheduler import stop_auto_apply_scheduler
    await stop_auto_apply_scheduler()


@app.get("/")
def root():
    return {"status": "ORDAL API v2 running"}
