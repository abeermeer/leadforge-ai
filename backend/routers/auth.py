"""POST /register, POST /login, GET /me"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from config import settings
from deps import get_current_user, get_db
from models import User, UserSettings
from schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from services import ratelimit
from services.auth_service import create_access_token, hash_password, verify_password

router = APIRouter()


def _rate_limit_auth(request: Request, email: str) -> None:
    """Throttle auth attempts per IP+email (audit 1.4). 429 when exceeded."""
    ip = ratelimit.client_ip(request)
    key = f"auth:{ip}:{email.lower()}"
    if not ratelimit.check(key, settings.RATE_LIMIT_AUTH_MAX, settings.RATE_LIMIT_AUTH_WINDOW_SEC):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    _rate_limit_auth(request, body.email)
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        name=body.name,
    )
    db.add(user)
    db.flush()
    # Settings row with defaults, ready for first use
    db.add(UserSettings(user_id=user.id))
    db.commit()

    return TokenResponse(access_token=create_access_token(str(user.id), user.token_version))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    _rate_limit_auth(request, body.email)
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(str(user.id), user.token_version))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
