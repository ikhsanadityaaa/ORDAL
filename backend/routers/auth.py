from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from database import get_db
from auth_utils import hash_password, verify_password, create_token

router = APIRouter()

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

@router.post("/register")
def register(req: RegisterRequest):
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
    if existing:
        db.close()
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = hash_password(req.password)
    cur = db.execute(
        "INSERT INTO users (email, password, name) VALUES (?, ?, ?)",
        (req.email, hashed, req.name)
    )
    db.commit()
    user_id = cur.lastrowid
    db.close()

    token = create_token(user_id, req.email)
    return {"token": token, "user": {"id": user_id, "email": req.email, "name": req.name}}

@router.post("/login")
def login(req: LoginRequest):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (req.email,)).fetchone()
    db.close()

    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user["id"], user["email"])
    return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}
