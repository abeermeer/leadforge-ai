"""Deliverability metrics + data-subject erasure endpoints (ops 3.1 / privacy 4.1).

Both are tenant-scoped through `get_current_user` — an operator can only see
and erase their own data, same rule as every other router.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from deps import get_current_user, get_db
from models import Campaign, Lead, Task, User
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


@router.get("/dashboard")
def dashboard(
    days: int = Query(default=30, ge=1, le=365),
    recent: int = Query(default=8, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything the Dashboard renders, in ONE request.

    The page used to fetch the campaign list and then leads + tasks for every
    campaign — 1 + 2N requests, repeated on a 5s poll while any agent ran. That
    is fine with two campaigns and pathological with fifty. Here the counts are
    done as GROUP BY aggregates in the database and only the rows actually shown
    (recent leads, recent tasks) are returned.
    """
    campaign_rows = db.query(Campaign).filter(Campaign.user_id == user.id).all()
    campaign_names = {c.id: c.name for c in campaign_rows}

    # Lead counts per status — aggregate, never a full row fetch.
    status_counts = {
        (status.value if hasattr(status, "value") else str(status)): count
        for status, count in db.query(Lead.status, func.count(Lead.id))
        .filter(Lead.user_id == user.id)
        .group_by(Lead.status)
        .all()
    }
    total_leads = sum(status_counts.values())

    # Leads created per day over the window, for the chart.
    since = datetime.utcnow() - timedelta(days=days)
    per_day = dict(
        db.query(func.date(Lead.created_at), func.count(Lead.id))
        .filter(Lead.user_id == user.id, Lead.created_at >= since)
        .group_by(func.date(Lead.created_at))
        .all()
    )
    series = []
    start = (datetime.utcnow() - timedelta(days=days - 1)).date()
    for i in range(days):
        day = start + timedelta(days=i)
        key = day.isoformat()
        series.append({"date": key, "leads": int(per_day.get(key, per_day.get(day, 0)) or 0)})

    recent_leads = (
        db.query(Lead)
        .filter(Lead.user_id == user.id)
        .order_by(Lead.created_at.desc())
        .limit(recent)
        .all()
    )

    tasks = (
        db.query(Task)
        .filter(Task.user_id == user.id)
        .order_by(Task.created_at.desc())
        .limit(50)
        .all()
    )

    return {
        "campaigns": len(campaign_rows),
        "total_leads": total_leads,
        "status_counts": status_counts,
        "series": series,
        "recent_leads": [
            {
                "id": str(l.id),
                "company_name": l.company_name,
                "campaign_name": campaign_names.get(l.campaign_id, "—"),
                "city": l.city,
                "status": l.status.value if hasattr(l.status, "value") else str(l.status),
                "fit_score": l.fit_score,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in recent_leads
        ],
        "tasks": [
            {
                "id": str(t.id),
                "type": t.type.value if hasattr(t.type, "value") else str(t.type),
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "total_items": t.total_items,
                "completed_items": t.completed_items,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ],
    }


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
