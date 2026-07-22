"""POST /register, POST /login, POST /logout, GET /me.

The access token is returned in the body (for API clients / tests) AND set as an
httpOnly cookie (audit 1.5) so the browser never keeps it in JavaScript-readable
storage. `deps.get_current_user` accepts either the cookie or a Bearer header.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from config import settings
from deps import COOKIE_NAME, get_current_user, get_db
from models import User, UserSettings
from schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from services import ratelimit
from services.auth_service import (
    create_access_token,
    create_verify_token,
    hash_password,
    read_verify_token,
    verify_password,
)

router = APIRouter()


def _rate_limit_auth(request: Request, email: str) -> None:
    """Throttle auth attempts per IP+email (audit 1.4). 429 when exceeded."""
    ip = ratelimit.client_ip(request)
    key = f"auth:{ip}:{email.lower()}"
    if not ratelimit.check(key, settings.RATE_LIMIT_AUTH_MAX, settings.RATE_LIMIT_AUTH_WINDOW_SEC):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=not settings.DEBUG,  # HTTPS-only in production
        max_age=settings.JWT_EXPIRY_HOURS * 3600,
        path="/",
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
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
    db.add(UserSettings(user_id=user.id))
    db.commit()

    token = create_access_token(str(user.id), user.token_version)
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    _rate_limit_auth(request, body.email)
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(str(user.id), user.token_version)
    _set_auth_cookie(response, token)
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(response: Response):
    """Clear the auth cookie."""
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"detail": "Logged out"}


@router.post("/logout-everywhere")
def logout_everywhere(
    response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Revoke every outstanding token by bumping the user's token_version."""
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"detail": "All sessions revoked"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/request-verification")
def request_verification(user: User = Depends(get_current_user)):
    """Issue an email-verification link. In production this is emailed to the
    user; here (and in dev) the link is returned so it can be delivered by
    whatever channel the deployment wires up.
    """
    if user.email_verified:
        return {"detail": "Already verified"}
    token = create_verify_token(str(user.id))
    link = f"{settings.APP_BASE_URL}/api/verify/{token}"
    # A production deployment sends `link` to user.email via its mail provider.
    return {"detail": "Verification link generated", "verification_link": link}


@router.get("/verify/{token}")
def verify_email(token: str, db: Session = Depends(get_db)):
    """Mark a user's email verified from a valid token (public link)."""
    import uuid as _uuid
    from datetime import datetime

    user_id = read_verify_token(token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    user = db.get(User, _uuid.UUID(str(user_id)))
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid verification link")
    user.email_verified = True
    user.email_verified_at = datetime.utcnow()
    db.commit()
    return {"detail": "Email verified", "email": user.email}


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """GDPR/CCPA: permanently delete the account and ALL of its data."""
    from models import (
        AgencyProfile,
        Campaign,
        EmailLog,
        Lead,
        SequenceStep,
        Suppression,
        Task,
        UsageCounter,
    )

    uid = user.id
    for model in (
        SequenceStep,
        EmailLog,
        Lead,
        Task,
        Campaign,
        AgencyProfile,
        Suppression,
        UsageCounter,
        UserSettings,
    ):
        db.query(model).filter(model.user_id == uid).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    response.delete_cookie("lf_access", path="/")
