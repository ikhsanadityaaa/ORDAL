import os
from cryptography.fernet import Fernet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENCRYPT_KEY_FILE = os.path.join(BASE_DIR, "encrypt.key")


def _get_fernet() -> Fernet:
    """
    Production: pakai ENCRYPTION_KEY dari environment variable.
    Local fallback: generate encrypt.key sekali untuk development.
    """
    env_key = os.getenv("ENCRYPTION_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode())

    if os.path.exists(_ENCRYPT_KEY_FILE):
        with open(_ENCRYPT_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip().encode()
        return Fernet(key)

    key = Fernet.generate_key()
    with open(_ENCRYPT_KEY_FILE, "wb") as f:
        f.write(key)
    print("encrypt.key dibuat otomatis untuk local development. Untuk production, isi ENCRYPTION_KEY di environment variable.")
    return Fernet(key)


def encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()
