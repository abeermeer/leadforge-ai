"""Reply detection (PRD 3D.5): IMAP poll + SendGrid inbound parse.

Matching a reply to a lead resolves via EmailLog.message_id (In-Reply-To /
References headers) or the sender address. On a match the lead flips to
'replied', its EmailLog is updated, and any scheduled follow-up steps cancel.
Never raises to the caller.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from crypto import decrypt_dict
from database import SessionLocal
from models import EmailLog, EmailLogStatus, Lead, LeadStatus, SequenceStep, SequenceStepStatus, UserSettings

logger = logging.getLogger("trax9.email.replies")


def _resolve_and_mark(db: Session, user_id, from_addr: str | None, ref_ids: list[str]) -> bool:
    """Find the lead a reply belongs to and mark it replied. True if matched."""
    log = None
    for mid in ref_ids:
        if not mid:
            continue
        log = (
            db.query(EmailLog)
            .filter(EmailLog.user_id == user_id, EmailLog.message_id.contains(mid.strip("<>")))
            .first()
        )
        if log:
            break
    if log is None and from_addr:
        log = (
            db.query(EmailLog)
            .filter(EmailLog.user_id == user_id, EmailLog.to_email == from_addr.lower())
            .order_by(EmailLog.sent_at.desc())
            .first()
        )
    if log is None:
        return False

    now = datetime.utcnow()
    log.status = EmailLogStatus.replied
    log.replied_at = now
    lead = db.get(Lead, log.lead_id)
    if lead is not None:
        lead.status = LeadStatus.replied
        lead.replied_at = now
        # cancel scheduled follow-ups — nobody gets chased after answering
        db.query(SequenceStep).filter(
            SequenceStep.lead_id == lead.id,
            SequenceStep.status == SequenceStepStatus.scheduled,
        ).update({SequenceStep.status: SequenceStepStatus.cancelled}, synchronize_session=False)
    db.commit()
    return True


async def poll_replies(user_id) -> dict:
    """Poll a user's IMAP inbox for unseen replies. Returns {'matched': n}."""
    db = SessionLocal()
    try:
        row = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not row or not row.imap_host or not row.imap_user:
            return {"matched": 0, "detail": "imap not configured"}
        password = decrypt_dict(row.encrypted_keys).get("imap_password")
        if not password:
            return {"matched": 0, "detail": "no imap password"}

        try:
            from imapclient import IMAPClient  # imported lazily
        except ImportError:
            logger.warning("imapclient not installed — reply polling disabled")
            return {"matched": 0, "detail": "imapclient missing"}

        matched = 0
        try:
            with IMAPClient(row.imap_host, use_uid=True, ssl=True) as client:
                client.login(row.imap_user, password)
                client.select_folder("INBOX")
                uids = client.search(["UNSEEN"])
                for uid, data in client.fetch(uids, ["ENVELOPE", "BODY[HEADER]"]).items():
                    env = data.get(b"ENVELOPE")
                    from_addr = None
                    if env and env.from_:
                        f = env.from_[0]
                        from_addr = f"{f.mailbox.decode()}@{f.host.decode()}".lower()
                    headers = (data.get(b"BODY[HEADER]") or b"").decode("utf-8", "ignore")
                    refs = _extract_refs(headers)
                    if _resolve_and_mark(db, user_id, from_addr, refs):
                        matched += 1
        except Exception as exc:
            logger.warning("IMAP poll failed for user %s: %s", user_id, exc)
            return {"matched": matched, "error": str(exc)}
        return {"matched": matched}
    finally:
        db.close()


def _extract_refs(headers: str) -> list[str]:
    """Pull In-Reply-To + References message-ids from a raw header block."""
    refs: list[str] = []
    for line in headers.splitlines():
        low = line.lower()
        if low.startswith("in-reply-to:") or low.startswith("references:"):
            refs += [tok for tok in line.split() if "@" in tok]
    return refs


def handle_inbound(db: Session, payload: dict) -> dict:
    """SendGrid inbound-parse handler: same match+update logic, synchronous."""
    from_addr = (payload.get("from") or "").lower() or None
    headers = payload.get("headers") or ""
    refs = _extract_refs(headers if isinstance(headers, str) else "")
    # inbound parse has no user scoping — resolve across the matched log's user
    log = None
    for mid in refs:
        log = db.query(EmailLog).filter(EmailLog.message_id.contains(mid.strip("<>"))).first()
        if log:
            break
    if log is None and from_addr:
        log = (
            db.query(EmailLog)
            .filter(EmailLog.to_email == from_addr)
            .order_by(EmailLog.sent_at.desc())
            .first()
        )
    if log is None:
        return {"matched": 0}
    return {"matched": 1 if _resolve_and_mark(db, log.user_id, from_addr, refs) else 0}
