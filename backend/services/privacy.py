"""Data-subject erasure + retention purging (privacy 4.1 / 4.2).

The rule that shapes both functions: **erasing someone must never un-suppress
them.** A naive "delete every row containing this address" also deletes the
suppression that stops us contacting them — so honouring a deletion request
would quietly re-open them to outreach, which is the opposite of what they
asked for.

Instead a purge blanks the plaintext address on the suppression row and keeps a
salted hash (`Suppression.email_hash`). `sender._is_suppressed` matches on that
hash, so the person stays permanently un-contactable while none of their
personal data remains.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from config import settings
from models import (
    AuditCache,
    EmailLog,
    Lead,
    SequenceStep,
    Suppression,
)
from services.email.identity import hash_email, normalize_email

logger = logging.getLogger("trax9.services.privacy")


def purge_email(db: Session, email: str, user_id=None) -> dict:
    """Erase every trace of `email`, keeping only a hashed suppression.

    Scoped to one tenant when `user_id` is given, otherwise global (an operator
    honouring a request that arrived out-of-band and could match any tenant).

    Returns a per-table count so the action is auditable — you can show a
    regulator exactly what was removed.
    """
    norm = normalize_email(email)
    if not norm:
        raise ValueError("email is required")
    digest = hash_email(norm)

    lead_q = db.query(Lead).filter(func.lower(Lead.email) == norm)
    if user_id is not None:
        lead_q = lead_q.filter(Lead.user_id == user_id)
    lead_ids = [row.id for row in lead_q.all()]

    counts = {"leads": 0, "email_logs": 0, "sequence_steps": 0, "audit_cache": 0, "suppressions_anonymized": 0}

    if lead_ids:
        counts["sequence_steps"] = (
            db.query(SequenceStep)
            .filter(SequenceStep.lead_id.in_(lead_ids))
            .delete(synchronize_session=False)
        )
        counts["email_logs"] = (
            db.query(EmailLog)
            .filter(EmailLog.lead_id.in_(lead_ids))
            .delete(synchronize_session=False)
        )

    # Email logs can also reference the address directly (e.g. a send that never
    # produced a surviving lead row).
    log_q = db.query(EmailLog).filter(func.lower(EmailLog.to_email) == norm)
    if user_id is not None:
        log_q = log_q.filter(EmailLog.user_id == user_id)
    counts["email_logs"] += log_q.delete(synchronize_session=False)

    # Drop cached audits for domains that existed only for the purged leads.
    domains = {
        (row.website or "").lower().replace("https://", "").replace("http://", "").strip("/")
        for row in lead_q.all()
    }
    domains.discard("")
    for domain in domains:
        still_used = (
            db.query(Lead)
            .filter(func.lower(Lead.website).like(f"%{domain}%"), ~Lead.id.in_(lead_ids))
            .first()
        )
        if still_used is None:
            counts["audit_cache"] += (
                db.query(AuditCache)
                .filter(func.lower(AuditCache.domain) == domain)
                .delete(synchronize_session=False)
            )

    if lead_ids:
        counts["leads"] = (
            db.query(Lead).filter(Lead.id.in_(lead_ids)).delete(synchronize_session=False)
        )

    # Anonymize — never delete — the suppression record.
    supp_q = db.query(Suppression).filter(
        or_(func.lower(Suppression.email) == norm, Suppression.email_hash == digest)
    )
    if user_id is not None:
        supp_q = supp_q.filter(Suppression.user_id == user_id)
    for supp in supp_q.all():
        supp.email_hash = digest
        supp.email = f"[erased:{digest[:12]}]"
        counts["suppressions_anonymized"] += 1

    db.commit()
    logger.info("privacy purge for hash=%s counts=%s", digest[:12], counts)
    return counts


def purge_expired_data(db: Session, months: int | None = None) -> dict:
    """Delete lead + email-log rows older than the retention window.

    Suppressions and usage counters are deliberately exempt: the first is a
    permanent legal record, the second is aggregate (non-personal) and needed
    for quota math.
    """
    window_months = settings.RETENTION_MONTHS if months is None else months
    cutoff = datetime.utcnow() - timedelta(days=int(window_months * 30.44))

    counts = {"cutoff": cutoff.isoformat(), "email_logs": 0, "sequence_steps": 0, "leads": 0}

    stale_lead_ids = [
        row.id for row in db.query(Lead.id).filter(Lead.created_at < cutoff).all()
    ]
    if stale_lead_ids:
        counts["sequence_steps"] = (
            db.query(SequenceStep)
            .filter(SequenceStep.lead_id.in_(stale_lead_ids))
            .delete(synchronize_session=False)
        )
        counts["email_logs"] = (
            db.query(EmailLog)
            .filter(EmailLog.lead_id.in_(stale_lead_ids))
            .delete(synchronize_session=False)
        )
        counts["leads"] = (
            db.query(Lead)
            .filter(Lead.id.in_(stale_lead_ids))
            .delete(synchronize_session=False)
        )

    counts["email_logs"] += (
        db.query(EmailLog).filter(EmailLog.created_at < cutoff).delete(synchronize_session=False)
    )

    db.commit()
    logger.info("retention purge older than %s: %s", cutoff.date(), counts)
    return counts
