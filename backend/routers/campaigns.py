"""Campaign CRUD, discovery trigger, task progress and lead listing (PRD §3F).

Every endpoint is scoped to current_user.id — a campaign belonging to another
user is a 404, never a 403 (no existence leaks across tenants).

POST /campaigns/{id}/discover normally enqueues tasks.discovery_tasks.
run_campaign_discovery. When the Celery broker (Redis) is unreachable it falls
back to running Google Maps + Google Custom Search synchronously inline and
saving through the same dedup + insert helper the worker path uses. Directory
scraping (Playwright) and email finding are queue-only — never run inside a
request.
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from deps import get_current_user, get_db, get_decrypted_keys, get_user_settings
from models import (
    AgencyProfile,
    Campaign,
    CampaignStatus,
    EmailLog,
    Lead,
    LeadStatus,
    SequenceStep,
    Task,
    TaskStatus,
    TaskType,
    User,
    UserSettings,
)
from schemas import CampaignCreate, CampaignOut, CampaignUpdate, LeadOut, Paginated, TaskOut
from services.discovery.google_maps import search_places
from services.discovery.google_search import search_google
from tasks.audit_tasks import (
    audit_campaign,
    create_audit_task_row,
    eligible_audit_leads,
    _audit_one,
)
from tasks.discovery_tasks import (
    create_discovery_task_row,
    find_emails_task,
    run_campaign_discovery,
    save_leads_for_campaign,
)

INLINE_AUDIT_CAP = 25

router = APIRouter()
logger = logging.getLogger("trax9.routers.campaigns")


def _get_campaign(db: Session, user: User, campaign_id: uuid.UUID) -> Campaign:
    """Fetch a campaign owned by the user, or 404."""
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.user_id == user.id)
        .first()
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


# ------------------------------------------------------------------ CRUD


@router.get("/campaigns", response_model=Paginated)
def list_campaigns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Campaign).filter(Campaign.user_id == user.id)
    total = query.count()
    rows = (
        query.order_by(Campaign.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Paginated(
        total=total,
        page=page,
        page_size=page_size,
        items=[CampaignOut.model_validate(row) for row in rows],
    )


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(
    body: CampaignCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a campaign; keywords/locations prefill from the agency profile.

    If the body omits keywords or locations and the user has an agency profile
    (the one referenced by agency_profile_id, else the latest), the missing
    lists are filled from profile.suggested_keywords / suggested_locations.
    """
    profile = None
    if body.agency_profile_id is not None:
        profile = (
            db.query(AgencyProfile)
            .filter(AgencyProfile.id == body.agency_profile_id, AgencyProfile.user_id == user.id)
            .first()
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="Agency profile not found")
    elif not body.seed_keywords or not body.target_locations:
        profile = (
            db.query(AgencyProfile)
            .filter(AgencyProfile.user_id == user.id)
            .order_by(AgencyProfile.updated_at.desc())
            .first()
        )

    seed_keywords = list(body.seed_keywords)
    target_locations = list(body.target_locations)
    if profile is not None:
        if not seed_keywords:
            seed_keywords = list(profile.suggested_keywords or [])
        if not target_locations:
            target_locations = list(profile.suggested_locations or [])

    campaign = Campaign(
        user_id=user.id,
        agency_profile_id=profile.id if profile is not None else None,
        name=body.name,
        seed_keywords=seed_keywords,
        target_locations=target_locations,
        industry_filters=list(body.industry_filters),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_campaign(db, user, campaign_id)


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    campaign = _get_campaign(db, user, campaign_id)
    for field in ("name", "seed_keywords", "target_locations", "industry_filters"):
        value = getattr(body, field)
        if value is not None:
            setattr(campaign, field, value)
    if body.status is not None:
        campaign.status = CampaignStatus(body.status)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a campaign and everything hanging off it (leads, tasks, logs)."""
    campaign = _get_campaign(db, user, campaign_id)

    lead_ids = select(Lead.id).where(Lead.campaign_id == campaign.id)
    db.query(SequenceStep).filter(SequenceStep.lead_id.in_(lead_ids)).delete(
        synchronize_session=False
    )
    db.query(EmailLog).filter(EmailLog.campaign_id == campaign.id).delete(
        synchronize_session=False
    )
    db.query(Lead).filter(Lead.campaign_id == campaign.id).delete(synchronize_session=False)
    db.query(Task).filter(Task.campaign_id == campaign.id).delete(synchronize_session=False)
    db.delete(campaign)
    db.commit()


# ------------------------------------------------------------------ discovery


@router.post("/campaigns/{campaign_id}/discover")
def discover_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    keys: dict = Depends(get_decrypted_keys),
    db: Session = Depends(get_db),
):
    """Trigger lead discovery: enqueue the Celery chord, or run inline if Redis is down."""
    campaign = _get_campaign(db, user, campaign_id)

    keywords = [k for k in (campaign.seed_keywords or []) if k]
    locations = [loc for loc in (campaign.target_locations or []) if loc]
    if not keywords or not locations:
        raise HTTPException(
            status_code=400,
            detail="Campaign needs seed keywords and target locations before discovery",
        )

    discovery_keys = {
        "google_places": keys.get("google_places"),
        "google_custom_search": keys.get("google_custom_search"),
        "google_custom_search_cx": keys.get("google_custom_search_cx"),
    }

    try:
        # retry=False + ignore_result=True: fail fast when the broker is down
        # instead of blocking the request in publish/result-backend retry loops.
        run_campaign_discovery.apply_async(
            args=(str(campaign.id), discovery_keys), retry=False, ignore_result=True
        )
        return {"detail": "Discovery queued", "mode": "queued"}
    except Exception as exc:  # kombu OperationalError etc. — broker unreachable
        logger.warning(
            "celery broker unavailable (%s) — running discovery inline",
            exc.__class__.__name__,
        )

    # Synchronous fallback: Maps + Custom Search only (API calls). Directory
    # scraping needs Playwright and email finding is slow per lead — both stay
    # queue-only rather than blocking an HTTP request for minutes.
    task_row = create_discovery_task_row(
        db, campaign, total_items=len(keywords) * len(locations) * 2
    )
    batches: list[list[dict]] = []
    failed = 0
    for keyword in keywords:
        for location in locations:
            try:
                batches.append(
                    asyncio.run(
                        search_places(
                            keyword, location, api_key=discovery_keys["google_places"]
                        )
                    )
                )
            except Exception:
                failed += 1
                logger.exception("inline google_maps failed for '%s' in '%s'", keyword, location)
            try:
                batches.append(
                    asyncio.run(
                        search_google(
                            keyword,
                            location,
                            api_key=discovery_keys["google_custom_search"],
                            cx=discovery_keys["google_custom_search_cx"],
                        )
                    )
                )
            except Exception:
                failed += 1
                logger.exception(
                    "inline google_search failed for '%s' in '%s'", keyword, location
                )

    new_leads = save_leads_for_campaign(db, campaign, batches)
    task_row.completed_items = task_row.total_items
    task_row.failed_items = failed
    task_row.status = TaskStatus.completed
    db.commit()

    try:
        find_emails_task.apply_async(
            args=(str(campaign.id),), retry=False, ignore_result=True
        )
    except Exception:
        logger.warning("broker still down — email finding skipped for %s", campaign.id)

    return {
        "detail": "Discovery ran inline (queue unavailable)",
        "mode": "inline",
        "new_leads": new_leads,
        "task_id": str(task_row.id),
    }


# ------------------------------------------------------------------ audit


def _audit_keys(settings_row: UserSettings, keys: dict) -> dict:
    """Assemble the decrypted key bundle the audit pipeline expects."""
    provider = (
        settings_row.ai_provider.value
        if hasattr(settings_row.ai_provider, "value")
        else settings_row.ai_provider
    )
    return {
        "provider": provider,
        "ai_key": keys.get(provider),
        "google_pagespeed": keys.get("google_pagespeed"),
        "socialcrawl": keys.get("socialcrawl"),
    }


@router.post("/campaigns/{campaign_id}/audit")
def audit_campaign_endpoint(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    keys: dict = Depends(get_decrypted_keys),
    settings_row: UserSettings = Depends(get_user_settings),
    db: Session = Depends(get_db),
):
    """Trigger the audit fan-out: enqueue the Celery chord, or run inline if Redis is down."""
    campaign = _get_campaign(db, user, campaign_id)

    audit_keys = _audit_keys(settings_row, keys)
    if not audit_keys["ai_key"]:
        raise HTTPException(
            status_code=400, detail="Configure your AI API key in Settings first"
        )

    try:
        audit_campaign.apply_async(
            args=(str(campaign.id), audit_keys), retry=False, ignore_result=True
        )
        return {"detail": "Audit queued", "mode": "queued"}
    except Exception as exc:  # broker unreachable
        logger.warning(
            "celery broker unavailable (%s) — running audit inline", exc.__class__.__name__
        )

    # Synchronous fallback (Redis down): audit up to INLINE_AUDIT_CAP leads in-request.
    leads = eligible_audit_leads(db, campaign)[:INLINE_AUDIT_CAP]
    if not leads:
        return {"detail": "No eligible leads to audit", "mode": "inline", "audited": 0}

    task_row = create_audit_task_row(db, campaign, total_items=len(leads))
    for lead in leads:
        _audit_one(db, lead, audit_keys)
        task_row.completed_items = (task_row.completed_items or 0) + 1
        db.commit()
    task_row.status = TaskStatus.completed
    db.commit()

    return {
        "detail": "Audit ran inline (queue unavailable)",
        "mode": "inline",
        "audited": len(leads),
        "task_id": str(task_row.id),
    }


# ------------------------------------------------------------------ send + export


@router.post("/campaigns/{campaign_id}/send")
def send_campaign_endpoint(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    keys: dict = Depends(get_decrypted_keys),
    settings_row: UserSettings = Depends(get_user_settings),
    db: Session = Depends(get_db),
):
    """Send all written/eligible leads: enqueue send fan-out, or run inline if Redis down."""
    from config import settings as _cfg
    from tasks.send_tasks import send_campaign_emails, send_one

    campaign = _get_campaign(db, user, campaign_id)
    if not keys.get("sendgrid"):
        raise HTTPException(status_code=400, detail="Configure your SendGrid API key in Settings first")

    provider = (
        settings_row.ai_provider.value
        if hasattr(settings_row.ai_provider, "value")
        else settings_row.ai_provider
    )
    send_keys = {"sendgrid": keys.get("sendgrid"), "ai_key": keys.get(provider), "provider": provider}

    try:
        send_campaign_emails.apply_async(
            args=(str(campaign.id), send_keys, _cfg.APP_BASE_URL), retry=False, ignore_result=True
        )
        return {"detail": "Send queued", "mode": "queued"}
    except Exception as exc:
        logger.warning("broker unavailable (%s) — sending inline", exc.__class__.__name__)

    leads = (
        db.query(Lead)
        .filter(
            Lead.campaign_id == campaign.id,
            Lead.status.in_(
                [LeadStatus.written, LeadStatus.scored, LeadStatus.enriched, LeadStatus.audited]
            ),
            Lead.email.isnot(None),
        )
        .limit(INLINE_AUDIT_CAP)
        .all()
    )
    if not leads:
        return {"detail": "No eligible leads to send", "mode": "inline", "sent": 0}

    task_row = Task(
        user_id=campaign.user_id, campaign_id=campaign.id, type=TaskType.send,
        status=TaskStatus.running, total_items=len(leads),
    )
    db.add(task_row)
    db.commit()
    sent = 0
    for lead in leads:
        res = send_one(db, lead, send_keys, _cfg.APP_BASE_URL)
        if res.get("status") == "sent":
            sent += 1
        task_row.completed_items = (task_row.completed_items or 0) + 1
        db.commit()
    task_row.status = TaskStatus.completed
    db.commit()
    return {"detail": "Sent inline (queue unavailable)", "mode": "inline", "sent": sent}


@router.get("/campaigns/{campaign_id}/export")
def export_campaign_leads(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """CSV export of the campaign's leads."""
    import csv
    import io

    from fastapi.responses import StreamingResponse

    campaign = _get_campaign(db, user, campaign_id)
    rows = (
        db.query(Lead)
        .filter(Lead.campaign_id == campaign.id)
        .order_by(Lead.fit_score.desc().nullslast())
        .all()
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "website", "email", "phone", "city", "status", "fit_score", "email_subject"])
    for r in rows:
        w.writerow([
            r.company_name, r.website, r.email or "", r.phone or "", r.city or "",
            r.status.value, r.fit_score if r.fit_score is not None else "", r.email_subject or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="campaign-{campaign_id}.csv"'},
    )


# ------------------------------------------------------------------ tasks + leads


@router.get("/campaigns/{campaign_id}/tasks", response_model=list[TaskOut])
def list_campaign_tasks(
    campaign_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_campaign(db, user, campaign_id)
    return (
        db.query(Task)
        .filter(Task.campaign_id == campaign_id, Task.user_id == user.id)
        .order_by(Task.created_at.desc())
        .all()
    )


@router.get("/campaigns/{campaign_id}/leads", response_model=Paginated)
def list_campaign_leads(
    campaign_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status: str | None = Query(None),
    search: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Campaign leads, filterable by status and company/website search.

    Ordered by fit_score desc (NULL scores last — expressed portably with a
    CASE so SQLite and Postgres behave identically), then newest first.
    """
    campaign = _get_campaign(db, user, campaign_id)

    query = db.query(Lead).filter(Lead.campaign_id == campaign.id, Lead.user_id == user.id)
    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown lead status {status!r}")
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(Lead.company_name.ilike(pattern), Lead.website.ilike(pattern))
        )

    total = query.count()
    score_nulls_last = case((Lead.fit_score.is_(None), 1), else_=0)
    rows = (
        query.order_by(score_nulls_last, Lead.fit_score.desc(), Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Paginated(
        total=total,
        page=page,
        page_size=page_size,
        items=[LeadOut.model_validate(row) for row in rows],
    )
