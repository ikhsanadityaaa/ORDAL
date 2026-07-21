import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from database import get_db
from auth_utils import get_current_user, decode_token
from workers.session_manager import session_manager

router = APIRouter()

@router.post("/start")
async def start_session(user=Depends(get_current_user)):
    """Mulai session apply manual (via HTTP API — dipakai untuk testing/debug)."""
    result = await session_manager.start_session_for_user(user_id=user["id"], source="manual")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Gagal memulai session"))
    return {"session_id": result["session_id"], "message": "Sesi dimulai"}

@router.post("/stop")
async def stop_session(user=Depends(get_current_user)):
    db = get_db()
    session = db.execute(
        "SELECT id FROM apply_sessions WHERE user_id = ? AND status = 'running'", (user["id"],)
    ).fetchone()
    if not session:
        db.close()
        await session_manager.stop_session(user["id"])
        return {"message": "Tidak ada sesi aktif, sinyal stop tetap dikirim"}
    db.execute("UPDATE apply_sessions SET status='stopped', ended_at=datetime('now') WHERE id=?", (session["id"],))
    db.commit()
    db.close()
    await session_manager.stop_session(user["id"])
    return {"message": "Sesi dihentikan"}

@router.get("/live")
async def live_updates(token: str = Query(...)):
    try:
        payload = decode_token(token)
        user = {"id": int(payload["sub"]), "email": payload["email"]}
    except Exception:
        raise HTTPException(status_code=401, detail="Token tidak valid")

    async def event_generator():
        queue = await session_manager.subscribe(user["id"])
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        break
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await session_manager.unsubscribe(user["id"])

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@router.get("/history")
def session_history(user=Depends(get_current_user)):
    db = get_db()
    sessions = db.execute(
        "SELECT * FROM apply_sessions WHERE user_id = ? ORDER BY started_at DESC LIMIT 20", (user["id"],)
    ).fetchall()
    result = []
    for s in sessions:
        logs = db.execute(
            "SELECT platform, COUNT(*) as count FROM apply_logs WHERE session_id = ? AND status = 'applied' AND confirmed_at IS NOT NULL GROUP BY platform",
            (s["id"],)
        ).fetchall()
        recent_logs = db.execute("""
            SELECT
                id, platform, job_title, company, job_url, position, location,
                job_location, salary, question_answers, status, skip_reason, applied_at
            FROM apply_logs
            WHERE session_id = ? AND status = 'applied' AND confirmed_at IS NOT NULL
            ORDER BY applied_at DESC
            LIMIT 10
        """, (s["id"],)).fetchall()
        result.append({
            **dict(s),
            "counts": {r["platform"]: r["count"] for r in logs},
            "logs": [dict(r) for r in recent_logs],
        })
    db.close()
    return result

@router.get("/applications")
def application_history(user=Depends(get_current_user)):
    db = get_db()
    rows = db.execute("""
        SELECT
            l.id,
            l.session_id,
            l.platform,
            l.job_title,
            l.company,
            l.job_url,
            l.position,
            l.location,
            l.job_location,
            l.salary,
            l.question_answers,
            l.confirmed_at,
            l.status,
            l.skip_reason,
            l.applied_at,
            s.started_at,
            s.ended_at,
            s.status AS session_status
        FROM apply_logs l
        JOIN apply_sessions s ON s.id = l.session_id
        WHERE s.user_id = ? AND l.status = 'applied' AND l.confirmed_at IS NOT NULL
        ORDER BY l.applied_at DESC
    """, (user["id"],)).fetchall()
    db.close()
    return [dict(r) for r in rows]

@router.get("/{session_id}/logs")
def session_logs(session_id: int, user=Depends(get_current_user)):
    db = get_db()
    session = db.execute(
        "SELECT id FROM apply_sessions WHERE id = ? AND user_id = ?", (session_id, user["id"])
    ).fetchone()
    if not session:
        db.close()
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    logs = db.execute(
        "SELECT * FROM apply_logs WHERE session_id = ? ORDER BY applied_at DESC", (session_id,)
    ).fetchall()
    db.close()
    return [dict(r) for r in logs]
