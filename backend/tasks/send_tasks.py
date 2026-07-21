"""Send fan-out (PRD §5 + 3D). Batched with per-batch countdown — NOT the
self-recursing/break pattern that only sends the first batch.
"""
import asyncio
import logging
import uuid

from database import SessionLocal
from models import (
    AgencyProfile,
    Campaign,
    Lead,
    LeadStatus,
    Task,
    TaskStatus,
    TaskType,
    User,
    UserSettings,
)
from services.ai.client import record_ai_usage
from services.email.sender import send_email
from services.email.sequencer import schedule_followups
from services.email.writer import generate_email
from tasks.celery_app import celery_app

logger = logging.getLogger("trax9.tasks.send")

BATCH_SIZE = 10
INTER_BATCH_SECONDS = 60


def _agency_services(db, user_id) -> list:
    p = (
        db.query(AgencyProfile)
        .filter(AgencyProfile.user_id == user_id)
        .order_by(AgencyProfile.updated_at.desc())
        .first()
    )
    return (p.services or []) if p else []


def send_one(db, lead: Lead, keys: dict, base_url: str) -> dict:
    """Generate the email if missing, then send. Schedules follow-ups on success."""
    user = db.get(User, lead.user_id)
    settings_row = db.query(UserSettings).filter(UserSettings.user_id == lead.user_id).first()
    sendgrid_key = keys.get("sendgrid")
    if not sendgrid_key:
        return {"status": "skipped", "reason": "no sendgrid key"}

    if not lead.email:
        return {"status": "skipped", "reason": "no email address"}

    if not (lead.email_subject and lead.email_body):
        ai_key = keys.get("ai_key")
        if not ai_key:
            return {"status": "skipped", "reason": "no ai key to generate email"}
        try:
            email, tokens = asyncio.run(
                generate_email(
                    {"company_name": lead.company_name, "website": lead.website},
                    lead.audit_data or {},
                    _agency_services(db, lead.user_id),
                    provider=keys.get("provider", "anthropic"),
                    api_key=ai_key,
                )
            )
            lead.email_subject = email["subject"]
            lead.email_body = email["body"]
            record_ai_usage(db, lead.user_id, tokens)
            lead.status = LeadStatus.written
            db.commit()
        except Exception as exc:
            logger.warning("email generation failed for lead %s: %s", lead.id, exc)
            return {"status": "failed", "reason": "generation failed"}

    result = send_email(db, lead, user, settings_row, sendgrid_key, base_url)
    if result.get("status") == "sent":
        schedule_followups(db, lead, lead.user_id)
    return result


@celery_app.task
def send_single_email(lead_id: str, keys: dict, base_url: str) -> dict:
    db = SessionLocal()
    try:
        lead = db.get(Lead, uuid.UUID(lead_id))
        if lead is None:
            return {"status": "missing"}
        res = send_one(db, lead, keys, base_url)
        _bump_send_progress(db, lead.campaign_id)
        return res
    finally:
        db.close()


def _bump_send_progress(db, campaign_id) -> None:
    row = (
        db.query(Task)
        .filter(
            Task.campaign_id == campaign_id,
            Task.type == TaskType.send,
            Task.status == TaskStatus.running,
        )
        .order_by(Task.created_at.desc())
        .first()
    )
    if row:
        row.completed_items = (row.completed_items or 0) + 1
        db.commit()


@celery_app.task
def send_campaign_emails(campaign_id: str, keys: dict, base_url: str) -> dict:
    """Enqueue one send task per written lead, batched with staggered countdowns."""
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, uuid.UUID(campaign_id))
        if campaign is None:
            return {"error": "campaign not found"}
        leads = (
            db.query(Lead)
            .filter(
                Lead.campaign_id == campaign.id,
                Lead.status.in_(
                    [LeadStatus.written, LeadStatus.scored, LeadStatus.enriched, LeadStatus.audited]
                ),
                Lead.email.isnot(None),
            )
            .all()
        )
        if not leads:
            return {"queued": 0}
        row = Task(
            user_id=campaign.user_id,
            campaign_id=campaign.id,
            type=TaskType.send,
            status=TaskStatus.running,
            total_items=len(leads),
        )
        db.add(row)
        db.commit()

        for idx, lead in enumerate(leads):
            countdown = (idx // BATCH_SIZE) * INTER_BATCH_SECONDS
            send_single_email.apply_async(
                args=(str(lead.id), keys, base_url), countdown=countdown
            )
        return {"queued": len(leads)}
    finally:
        db.close()
