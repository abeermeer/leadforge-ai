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


def create_access_token(user_id: str, token_version: int = 0) -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRY_HOURS)
    payload = {"sub": str(user_id), "exp": expire, "ver": token_version}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_verify_token(user_id: str) -> str:
    """A 48h single-purpose token for email verification."""
    expire = datetime.utcnow() + timedelta(hours=48)
    payload = {"sub": str(user_id), "exp": expire, "purpose": "verify"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def read_verify_token(token: str) -> str | None:
    """Return the user_id from a valid verify token, else None."""
    from jose import JWTError

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
    if payload.get("purpose") != "verify":
        return None
    return payload.get("sub")
