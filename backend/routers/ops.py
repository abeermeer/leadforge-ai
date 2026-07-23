"""Deliverability metrics + data-subject erasure endpoints (ops 3.1 / privacy 4.1).

Both are tenant-scoped through `get_current_user` — an operator can only see
and erase their own data, same rule as every other router.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from deps import get_current_user, get_db
from models import User
from services import metrics, privacy

router = APIRouter()


class PurgeRequest(BaseModel):
    email: EmailStr


@router.get("/metrics/summary")
def metrics_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sent / delivered / bounced / replied / spam — totals, per day, per campaign."""
    return metrics.summary(db, user.id, days=days)


@router.get("/metrics/health")
def metrics_health(
    hours: int = Query(default=24, ge=1, le=168),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trailing-window deliverability health (what the alert job thresholds on)."""
    return metrics.health_snapshot(db, user.id, hours=hours)


@router.post("/privacy/purge")
def purge_contact(
    body: PurgeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Erase every trace of an address from this account (GDPR/CCPA erasure).

    The suppression record survives in hashed form — erasing someone must not
    silently make them contactable again.
    """
    try:
        counts = privacy.purge_email(db, str(body.email), user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"detail": "Contact erased", "email": str(body.email), "purged": counts}
