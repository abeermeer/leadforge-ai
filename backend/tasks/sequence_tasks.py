"""Beat-driven tasks (PRD 3E, 3D, 3D.5): warmup ramp, follow-ups, reply polling."""
import asyncio
import logging
from datetime import datetime

from celery.schedules import crontab

from config import settings
from database import SessionLocal
from models import (
    Lead,
    LeadStatus,
    SequenceStep,
    SequenceStepStatus,
    User,
    UserSettings,
)
from services.email.reply_tracker import poll_replies
from services.email.sender import send_email
from tasks.celery_app import celery_app

logger = logging.getLogger("trax9.tasks.sequence")

# Leads that must NOT receive follow-ups
_STOP_STATUSES = {
    LeadStatus.replied,
    LeadStatus.unsubscribed,
    LeadStatus.bounced,
    LeadStatus.failed,
}


@celery_app.task
def raise_warmup_caps() -> dict:
    """Daily: ramp each warmup-enabled user's daily cap toward their real max."""
    db = SessionLocal()
    try:
        rows = db.query(UserSettings).filter(UserSettings.warmup_enabled.is_(True)).all()
        bumped = 0
        for row in rows:
            new_cap = min(
                row.max_emails_per_day, (row.warmup_daily_cap or 0) + settings.WARMUP_DAILY_INCREMENT
            )
            if new_cap != row.warmup_daily_cap:
                row.warmup_daily_cap = new_cap
                bumped += 1
        db.commit()
        return {"bumped": bumped}
    finally:
        db.close()


@celery_app.task
def run_due_sequences() -> dict:
    """Every 15 min: send follow-up steps that are due and still valid."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        steps = (
            db.query(SequenceStep)
            .filter(
                SequenceStep.status == SequenceStepStatus.scheduled,
                SequenceStep.scheduled_for <= now,
            )
            .all()
        )
        sent = 0
        for step in steps:
            lead = db.get(Lead, step.lead_id)
            if lead is None or lead.status in _STOP_STATUSES:
                step.status = SequenceStepStatus.skipped
                db.commit()
                continue
            try:
                user = db.get(User, step.user_id)
                srow = db.query(UserSettings).filter(UserSettings.user_id == step.user_id).first()
                keys = _keys_for(srow)
                if not keys.get("sendgrid"):
                    continue
                # short templated follow-up; body filled here so send is self-contained
                subject = step.subject or f"Following up — {lead.company_name}"
                body = step.body or _followup_body(step.step_number, lead)
                lead.email_subject, lead.email_body = subject, body
                db.commit()
                res = send_email(
                    db, lead, user, srow, keys["sendgrid"], settings.APP_BASE_URL,
                    sequence_step=step.step_number,
                )
                if res.get("status") == "sent":
                    step.status = SequenceStepStatus.sent
                    step.sent_at = datetime.utcnow()
                    sent += 1
                    db.commit()
            except Exception:
                logger.exception("follow-up step %s failed", step.id)
        return {"sent": sent, "due": len(steps)}
    finally:
        db.close()


@celery_app.task
def poll_all_replies() -> dict:
    """Every 5 min: poll IMAP for each user who configured a host."""
    db = SessionLocal()
    try:
        users = db.query(UserSettings).filter(UserSettings.imap_host.isnot(None)).all()
        total = 0
        for row in users:
            try:
                total += asyncio.run(poll_replies(row.user_id)).get("matched", 0)
            except Exception:
                logger.exception("reply poll failed for user %s", row.user_id)
        return {"matched": total}
    finally:
        db.close()


def _keys_for(settings_row) -> dict:
    from crypto import decrypt_dict

    if settings_row is None:
        return {}
    keys = decrypt_dict(settings_row.encrypted_keys)
    provider = (
        settings_row.ai_provider.value
        if hasattr(settings_row.ai_provider, "value")
        else settings_row.ai_provider
    )
    return {"sendgrid": keys.get("sendgrid"), "ai_key": keys.get(provider), "provider": provider}


def _followup_body(step: int, lead) -> str:
    if step == 1:
        return (
            f"Just floating this back to the top of your inbox — I ran a quick audit of "
            f"{lead.company_name}'s site and think there are a couple of fast wins worth 15 minutes.\n\n"
            "Happy to send the findings over if useful.\n\nAyesha | Client Consultant, Trax9"
        )
    return (
        f"I'll close the loop here — if improving {lead.company_name}'s online presence isn't a "
        "priority right now, no worries at all. Reply anytime and I'll pick it back up.\n\n"
        "Ayesha | Client Consultant, Trax9"
    )


celery_app.conf.beat_schedule = {
    "raise-warmup-caps": {"task": "tasks.sequence_tasks.raise_warmup_caps", "schedule": crontab(hour=0, minute=5)},
    "run-due-sequences": {"task": "tasks.sequence_tasks.run_due_sequences", "schedule": crontab(minute="*/15")},
    "poll-all-replies": {"task": "tasks.sequence_tasks.poll_all_replies", "schedule": crontab(minute="*/5")},
}
