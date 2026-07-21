"""FastAPI dependencies: DB session, current user, decrypted user settings."""
import uuid
from typing import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from config import settings
from crypto import decrypt_dict
from database import SessionLocal
from models import User, UserSettings

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise unauthorized
    except JWTError:
        raise unauthorized

    user = db.get(User, uuid.UUID(user_id))
    if user is None:
        raise unauthorized
    return user


def get_user_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSettings:
    """Return the user's settings row, creating defaults on first access."""
    row = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if row is None:
        row = UserSettings(user_id=user.id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_decrypted_keys(user_settings: UserSettings = Depends(get_user_settings)) -> dict:
    """Decrypted third-party API keys. Use at the moment of an external call only."""
    return decrypt_dict(user_settings.encrypted_keys)
