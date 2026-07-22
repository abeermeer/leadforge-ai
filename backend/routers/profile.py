"""POST /profile/analyze, GET /profile — Agency Brain (PRD §3.0)."""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from deps import get_current_user, get_db, get_decrypted_keys, get_user_settings
from models import AgencyProfile, User, UserSettings
from schemas import ProfileAnalyzeRequest, ProfileOut
from services import ratelimit
from services.net.safe_http import UnsafeURLError
from services.profile.agency_analyzer import analyze_agency

router = APIRouter()


@router.post("/profile/analyze", response_model=ProfileOut)
async def analyze_profile(
    body: ProfileAnalyzeRequest,
    user: User = Depends(get_current_user),
    user_settings: UserSettings = Depends(get_user_settings),
    keys: dict = Depends(get_decrypted_keys),
    db: Session = Depends(get_db),
):
    """Scrape the agency website, run AI analysis inline, upsert + return the profile."""
    # Per-user throttle: this endpoint hits arbitrary external hosts — don't let
    # it double as a port scanner even after the SSRF guard (audit 1.4).
    if not ratelimit.check(
        f"analyze:{user.id}",
        settings.RATE_LIMIT_ANALYZE_MAX,
        settings.RATE_LIMIT_ANALYZE_WINDOW_SEC,
    ):
        raise HTTPException(status_code=429, detail="Too many analyses — try again later")

    provider = (
        user_settings.ai_provider.value
        if hasattr(user_settings.ai_provider, "value")
        else user_settings.ai_provider
    )
    api_key = keys.get(provider)
    if not api_key:
        raise HTTPException(status_code=400, detail="Configure your AI API key in Settings first")

    try:
        return await analyze_agency(
            body.website, user.id, db, provider=provider, api_key=api_key
        )
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=f"That URL is not allowed: {exc}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream returned {exc.response.status_code} while analyzing the website",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the website or AI provider: {exc.__class__.__name__}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Analysis failed: {exc}")


@router.get("/profile", response_model=ProfileOut)
def get_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Latest saved agency profile for the current user."""
    row = (
        db.query(AgencyProfile)
        .filter(AgencyProfile.user_id == user.id)
        .order_by(AgencyProfile.updated_at.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No agency profile yet — run /profile/analyze")
    return row
