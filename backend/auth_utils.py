import os
import jwt
import bcrypt
from datetime import datetime, timedelta
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_KEY_FILE = os.path.join(BASE_DIR, "secret.key")


def _get_secret() -> str:
    """
    Production: pakai JWT_SECRET dari environment variable.
    Local fallback: generate secret.key sekali untuk development.
    """
    env_secret = os.getenv("JWT_SECRET", "").strip()
    if env_secret:
        return env_secret

    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()

    import secrets
    key = secrets.token_hex(32)
    with open(_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    print("secret.key dibuat otomatis untuk local development. Untuk production, isi JWT_SECRET di environment variable.")
    return key


SECRET_KEY = _get_secret()
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

security = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    payload = decode_token(credentials.credentials)
    return {"id": int(payload["sub"]), "email": payload["email"]}
