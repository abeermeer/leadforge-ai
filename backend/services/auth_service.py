"""JWT creation/validation + password hashing."""
from datetime import datetime, timedelta

import bcrypt
from jose import jwt

from config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRY_HOURS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
