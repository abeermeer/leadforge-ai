"""Deliverability metrics (ops 3.1).

Answers "how did last week's sending go" from `email_logs` alone. Deliberately
plain SQL aggregation — no Prometheus, no time-series store. The number that
matters is the bounce rate, because ESPs suspend accounts over it and they tell
you *after* the damage.

Note on `delivered`: SendGrid reports bounces as events, so anything sent that
did not bounce is treated as delivered. That is an upper bound, not a
guarantee — it cannot see silent spam-foldering.
"""
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Campaign, EmailLog, EmailLogStatus

# Every status that represents a message we actually handed to the ESP.
_ATTEMPTED = (
    EmailLogStatus.sent,
    EmailLogStatus.opened,
    EmailLogStatus.clicked,
    EmailLogStatus.replied,
    EmailLogStatus.bounced,
    EmailLogStatus.spam,
)


def _rate(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 2) if denominator else 0.0


def summary(db: Session, user_id, days: int = 30) -> dict:
    """Totals, per-day series, and per-campaign breakdown for the window."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(EmailLog)
        .filter(EmailLog.user_id == user_id, EmailLog.created_at >= since)
        .all()
    )

    def tally(subset):
        attempted = [r for r in subset if r.status in _ATTEMPTED]
        bounced = [r for r in subset if r.status == EmailLogStatus.bounced]
        spam = [r for r in subset if r.status == EmailLogStatus.spam]
        replied = [r for r in subset if r.status == EmailLogStatus.replied or r.replied_at]
        opened = [r for r in subset if r.status == EmailLogStatus.opened or r.opened_at]
        sent_n = len(attempted)
        return {
            "sent": sent_n,
            "delivered": sent_n - len(bounced),
            "bounced": len(bounced),
            "opened": len(opened),
            "replied": len(replied),
            "spam": len(spam),
            "queued": len([r for r in subset if r.status == EmailLogStatus.queued]),
            "bounce_rate": _rate(len(bounced), sent_n),
            "reply_rate": _rate(len(replied), sent_n),
            "spam_rate": _rate(len(spam), sent_n),
        }

    # Per-day series (oldest first) so the UI can chart it directly.
    by_day: dict[str, list] = {}
    for r in rows:
        stamp = r.sent_at or r.created_at
        by_day.setdefault(stamp.strftime("%Y-%m-%d"), []).append(r)
    series = [{"date": day, **tally(items)} for day, items in sorted(by_day.items())]

    # Per-campaign breakdown, worst bounce rate first — that's the one to kill.
    names = {c.id: c.name for c in db.query(Campaign).filter(Campaign.user_id == user_id).all()}
    by_campaign: dict = {}
    for r in rows:
        by_campaign.setdefault(r.campaign_id, []).append(r)
    campaigns = [
        {"campaign_id": str(cid), "name": names.get(cid, "—"), **tally(items)}
        for cid, items in by_campaign.items()
    ]
    campaigns.sort(key=lambda c: c["bounce_rate"], reverse=True)

    return {
        "window_days": days,
        "since": since.isoformat(),
        "totals": tally(rows),
        "series": series,
        "campaigns": campaigns,
    }


def health_snapshot(db: Session, user_id, hours: int = 24) -> dict:
    """Trailing-window numbers the alerting job thresholds against."""
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(EmailLog)
        .filter(EmailLog.user_id == user_id, EmailLog.created_at >= since)
        .all()
    )
    attempted = [r for r in rows if r.status in _ATTEMPTED]
    bounced = [r for r in rows if r.status == EmailLogStatus.bounced]
    spam = [r for r in rows if r.status == EmailLogStatus.spam]
    return {
        "window_hours": hours,
        "attempted": len(attempted),
        "bounced": len(bounced),
        "spam": len(spam),
        "bounce_rate": _rate(len(bounced), len(attempted)),
    }
