"""Public webhooks (PRD 3D webhook + 3D.5): SendGrid events, inbound replies, unsubscribe.

Security (audit 1.2/1.4): the event webhook verifies SendGrid's ECDSA signature
when a verification key is configured; the inbound endpoint is gated behind a
per-deployment secret in its path; both are rate-limited per IP.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from config import settings
from deps import get_db
from models import (
    EmailLog,
    EmailLogStatus,
    Lead,
    LeadStatus,
    Suppression,
    SuppressionReason,
)
from services import ratelimit
from services.email.reply_tracker import handle_inbound
from services.email.sender import verify_unsub_token
from services.email.sequencer import cancel_sequence

router = APIRouter()
logger = logging.getLogger("trax9.webhooks")


def _rate_limit_webhook(request: Request) -> None:
    ip = ratelimit.client_ip(request)
    if not ratelimit.check(
        f"wh:{ip}", settings.RATE_LIMIT_WEBHOOK_MAX, settings.RATE_LIMIT_WEBHOOK_WINDOW_SEC
    ):
        raise HTTPException(status_code=429, detail="Too many requests")


def _verify_sendgrid_signature(raw_body: bytes, request: Request) -> None:
    """Verify the SendGrid Event Webhook ECDSA signature. 401 on failure.

    Skipped (with a warning) only when no verification key is configured — that
    path is for local dev, never production.
    """
    key = settings.SENDGRID_WEBHOOK_VERIFICATION_KEY
    if not key:
        logger.warning("SENDGRID_WEBHOOK_VERIFICATION_KEY unset — skipping signature check (dev)")
        return
    from sendgrid.helpers.eventwebhook import EventWebhook, EventWebhookHeader

    signature = request.headers.get(EventWebhookHeader.SIGNATURE)
    timestamp = request.headers.get(EventWebhookHeader.TIMESTAMP)
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    try:
        ew = EventWebhook()
        ecdsa_key = ew.convert_public_key_to_ecdsa(key)
        ok = ew.verify_signature(raw_body.decode("utf-8"), signature, timestamp, ecdsa_key)
    except Exception as exc:
        logger.warning("webhook signature verification error: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not ok:
        logger.warning("webhook signature verification FAILED from %s", ratelimit.client_ip(request))
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _suppress(db: Session, user_id, email: str, reason: SuppressionReason) -> None:
    if not email:
        return
    exists = (
        db.query(Suppression)
        .filter(Suppression.user_id == user_id, Suppression.email == email.lower())
        .first()
    )
    if exists is None:
        db.add(Suppression(user_id=user_id, email=email.lower(), reason=reason))


@router.post("/webhook/email")
async def sendgrid_events(request: Request, db: Session = Depends(get_db)):
    """SendGrid event webhook. Verify signature, dedup by sg_event_id, respond 200 fast."""
    _rate_limit_webhook(request)
    raw = await request.body()
    _verify_sendgrid_signature(raw, request)  # 401 before touching the DB
    import json as _json

    try:
        events = _json.loads(raw)
    except Exception:
        return {"ok": True}
    if not isinstance(events, list):
        events = [events]

    for ev in events:
        mid = ev.get("sg_message_id", "").split(".")[0]
        event = ev.get("event")
        sg_event_id = ev.get("sg_event_id")
        email_addr = ev.get("email", "")
        log = (
            db.query(EmailLog)
            .filter(EmailLog.message_id.contains(mid))
            .order_by(EmailLog.sent_at.desc().nullslast())
            .first()
            if mid
            else None
        )
        if log is None and email_addr:
            log = (
                db.query(EmailLog)
                .filter(EmailLog.to_email == email_addr.lower())
                .order_by(EmailLog.sent_at.desc())
                .first()
            )
        if log is None:
            continue
        # dedup
        seen = log.sg_event_ids or []
        if sg_event_id and sg_event_id in seen:
            continue
        if sg_event_id:
            log.sg_event_ids = seen + [sg_event_id]

        lead = db.get(Lead, log.lead_id)
        now = datetime.utcnow()
        if event == "open":
            log.status = EmailLogStatus.opened
            log.opened_at = now
            if lead and lead.status == LeadStatus.sent:
                lead.status, lead.opened_at = LeadStatus.opened, now
        elif event == "click":
            log.status = EmailLogStatus.clicked
        elif event in ("bounce", "dropped"):
            log.status = EmailLogStatus.bounced
            log.bounce_reason = ev.get("reason")
            if lead:
                lead.status = LeadStatus.bounced
                _suppress(db, log.user_id, email_addr, SuppressionReason.bounce)
                cancel_sequence(db, lead.id)
        elif event == "spamreport":
            log.status = EmailLogStatus.spam
            _suppress(db, log.user_id, email_addr, SuppressionReason.spam)
            if lead:
                cancel_sequence(db, lead.id)
        elif event in ("unsubscribe", "group_unsubscribe"):
            _suppress(db, log.user_id, email_addr, SuppressionReason.unsubscribe)
            if lead:
                lead.status = LeadStatus.unsubscribed
                cancel_sequence(db, lead.id)
    db.commit()
    return {"ok": True}


@router.post("/webhook/inbound/{secret}")
async def inbound_parse(secret: str, request: Request, db: Session = Depends(get_db)):
    """SendGrid inbound parse -> reply detection. Gated behind a path secret (audit 1.2)."""
    _rate_limit_webhook(request)
    configured = settings.INBOUND_WEBHOOK_SECRET
    # If a secret is configured it must match; if not configured, 404 so the
    # endpoint is never triggerable by an outsider guessing a lead's email.
    import hmac as _hmac

    if not configured or not _hmac.compare_digest(secret, configured):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        form = await request.form()
        payload = {k: v for k, v in form.items()}
    except Exception:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
    return handle_inbound(db, payload)


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(get_db)):
    """One-click unsubscribe (public). Always returns a friendly page."""
    result = verify_unsub_token(token)
    if result is not None:
        import uuid as _uuid

        user_id_raw, email = result
        try:
            user_id = _uuid.UUID(str(user_id_raw))
        except (ValueError, AttributeError):
            user_id = user_id_raw
        _suppress(db, user_id, email, SuppressionReason.unsubscribe)
        # flip any active lead for this address + cancel its follow-up sequence
        affected = (
            db.query(Lead).filter(Lead.user_id == user_id, Lead.email == email.lower()).all()
        )
        for lead in affected:
            lead.status = LeadStatus.unsubscribed
            cancel_sequence(db, lead.id)
        db.commit()
    return HTMLResponse(
        "<html><body style='font-family:system-ui;background:#070b16;color:#e6e9f2;"
        "display:grid;place-items:center;height:100vh;margin:0'>"
        "<div style='text-align:center'><h1 style='color:#f5a623'>You're unsubscribed</h1>"
        "<p style='color:#8b94ab'>You won't receive further emails from us.</p></div></body></html>"
    )
