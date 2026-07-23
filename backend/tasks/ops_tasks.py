"""Scheduled ops jobs: deliverability alerting + retention purge (3.2 / 4.2).

Alerts are emitted at ERROR level so they reach Sentry through the existing
LoggingIntegration without any extra wiring, and are additionally emailed when
ALERT_EMAIL is set. Nothing here raises — a monitoring job that crashes the
beat worker is worse than a missed alert.
"""
import logging

from celery.schedules import crontab

from config import settings
from database import SessionLocal
from models import User
from services import metrics, privacy
from tasks.celery_app import celery_app

logger = logging.getLogger("trax9.tasks.ops")


def _redis():
    try:
        import redis

        return redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
    except Exception:
        return None


def _queue_depths() -> dict:
    """Pending message count per Celery queue (0 when Redis is unreachable)."""
    client = _redis()
    if client is None:
        return {}
    depths = {}
    for queue in ("profile", "discovery", "email_find", "audit", "email"):
        try:
            depths[queue] = int(client.llen(queue) or 0)
        except Exception:
            depths[queue] = 0
    return depths


def _notify(subject: str, body: str) -> None:
    """Log at ERROR (→ Sentry) and email the operator when configured."""
    logger.error("ALERT: %s — %s", subject, body)
    if not settings.ALERT_EMAIL:
        return
    db = SessionLocal()
    try:
        from crypto import decrypt_dict
        from models import UserSettings

        row = db.query(UserSettings).first()
        keys = decrypt_dict(row.encrypted_keys) if row else {}
        api_key = keys.get("sendgrid")
        if not api_key:
            return
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sendgrid.SendGridAPIClient(api_key).send(
            Mail(
                from_email=(row.from_email or settings.ALERT_EMAIL),
                to_emails=settings.ALERT_EMAIL,
                subject=f"[LeadForge] {subject}",
                plain_text_content=body,
            )
        )
    except Exception as exc:  # alerting must never break the beat worker
        logger.warning("alert email failed: %s", exc)
    finally:
        db.close()


@celery_app.task
def check_deliverability_alerts() -> dict:
    """Threshold bounce rate, spam complaints, and queue depth; alert on breach."""
    db = SessionLocal()
    fired = []
    try:
        for user in db.query(User).all():
            snap = metrics.health_snapshot(db, user.id, hours=settings.ALERT_WINDOW_HOURS)
            # Below the sample floor a single bounce reads as a huge percentage.
            if (
                snap["attempted"] >= settings.ALERT_MIN_SAMPLE
                and snap["bounce_rate"] > settings.ALERT_BOUNCE_RATE_PCT
            ):
                fired.append(
                    {"user": user.email, "kind": "bounce_rate", **snap}
                )
                _notify(
                    f"Bounce rate {snap['bounce_rate']}% for {user.email}",
                    f"{snap['bounced']}/{snap['attempted']} bounced in the last "
                    f"{snap['window_hours']}h (threshold {settings.ALERT_BOUNCE_RATE_PCT}%). "
                    "Pause sending and check list quality before the ESP does it for you.",
                )
            if snap["spam"] > settings.ALERT_SPAM_REPORT_MAX:
                fired.append({"user": user.email, "kind": "spam", **snap})
                _notify(
                    f"{snap['spam']} spam complaint(s) for {user.email}",
                    "Spam complaints damage domain reputation disproportionately. "
                    "Review the copy and the source of these addresses.",
                )

        depths = _queue_depths()
        for queue, depth in depths.items():
            if depth > settings.ALERT_QUEUE_DEPTH_MAX:
                fired.append({"kind": "queue_depth", "queue": queue, "depth": depth})
                _notify(
                    f"Celery queue '{queue}' backed up ({depth})",
                    f"Depth {depth} exceeds {settings.ALERT_QUEUE_DEPTH_MAX}. "
                    "Workers may be down or a stage is failing and retrying.",
                )

        return {"alerts": len(fired), "detail": fired, "queues": depths}
    finally:
        db.close()


@celery_app.task
def purge_expired_data() -> dict:
    """Nightly retention purge. No-op when RETENTION_PURGE_ENABLED is false."""
    if not settings.RETENTION_PURGE_ENABLED:
        return {"skipped": "retention purge disabled"}
    db = SessionLocal()
    try:
        return privacy.purge_expired_data(db)
    finally:
        db.close()


# .update() so this composes with tasks.sequence_tasks' schedule.
celery_app.conf.beat_schedule.update(
    {
        "check-deliverability-alerts": {
            "task": "tasks.ops_tasks.check_deliverability_alerts",
            "schedule": crontab(minute="*/30"),
        },
        "purge-expired-data": {
            "task": "tasks.ops_tasks.purge_expired_data",
            "schedule": crontab(hour=3, minute=20),
        },
    }
)
