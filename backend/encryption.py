import os
from cryptography.fernet import Fernet

_ENCRYPT_KEY_FILE = "encrypt.key"


def _get_fernet() -> Fernet:
    env_key = os.getenv("ENCRYPTION_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode())
    if os.path.exists(_ENCRYPT_KEY_FILE):
        key = open(_ENCRYPT_KEY_FILE).read().strip().encode()
        return Fernet(key)
    key = Fernet.generate_key()
    with open(_ENCRYPT_KEY_FILE, "wb") as f:
        f.write(key)
    print("encrypt.key dibuat otomatis. Untuk production, isi ENCRYPTION_KEY di .env")
    return Fernet(key)


def encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()
