"""Email sending via SendGrid — rate limits, warmup, suppression, CAN-SPAM (PRD 3D).

Keys and sending config are per-user (from `user_settings`, decrypted at call
time by the caller). Nothing here reads a global SendGrid key.

Design notes:
- **Rate-limit state lives in Redis, never in process globals** so counters are
  correct across every worker and survive restarts. Two entry points, and the
  difference matters:
    * `check_rate_limit` — read-only, ADVISORY. Safe for a "can I send?" preflight,
      but it cannot enforce: with multiple workers, two can both read 99/100 and
      both send.
    * `reserve_send_slot` — ENFORCING, and what `send_email` uses. Increments
      first (atomic in Redis) and tests the resulting value, so only one racer
      can cross the cap. Anything that then fails to put a message on the wire
      calls `_release_send_slot` to give the slot back, so a blocked, bounced or
      crashed attempt never burns quota.
  Keys `sent:{uid}:{YYYY-MM-DD-HH}` and `sent:{uid}:{YYYY-MM-DD}` carry TTLs. If
  Redis is unreachable the limiter fails OPEN (allows the send and logs) so local
  dev works without Redis.
- The **monthly quota** (usage_counters.emails_sent vs users.monthly_email_quota)
  is enforced inside `send_email`, not `check_rate_limit`, because it needs the DB
  session and the User row; `check_rate_limit`'s signature is intentionally
  DB-free and covers only the Redis-backed window/hourly/daily limits.
- Every email carries a one-click unsubscribe (signed HMAC token, itsdangerous-
  free) plus the account's physical postal address, and the `List-Unsubscribe` /
  `List-Unsubscribe-Post` headers.

No function here raises to its caller — failures are logged and returned as a
status dict / (False, reason) tuple.
"""
import base64
import hashlib
import hmac
import html
import logging
import time
from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings
from services.email.identity import hash_email, normalize_email
from models import (
    EmailLog,
    EmailLogStatus,
    Lead,
    LeadStatus,
    Suppression,
    SuppressionReason,
    UsageCounter,
    User,
    UserSettings,
)

logger = logging.getLogger("trax9.services.email.sender")

# Redis key TTLs — a little longer than the window they guard so a counter never
# expires mid-window but still self-cleans.
_HOUR_TTL_SECONDS = 2 * 3600        # 2h covers a 1h bucket
_DAY_TTL_SECONDS = 48 * 3600        # 48h covers a 1d bucket

# 5xx / network retry policy.
_MAX_SEND_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 2


# --------------------------------------------------------------------------- redis


def _redis():
    """Return a live Redis client, or None if Redis is unreachable.

    Imported lazily so the module (and the whole app) stays importable without a
    running Redis. Callers treat None as "fail open" for limits.
    """
    try:
        import redis  # local import: keep module importable without redis running

        client = redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        return client
    except Exception as exc:  # ImportError, ConnectionError, TimeoutError, ...
        logger.warning("redis unavailable (%s) — rate limiter failing open", exc)
        return None


def _hour_key(user_id) -> str:
    return f"sent:{user_id}:{datetime.utcnow():%Y-%m-%d-%H}"


def _day_key(user_id) -> str:
    return f"sent:{user_id}:{datetime.utcnow():%Y-%m-%d}"


def _incr_send_counters(user_id) -> None:
    """Increment the hourly + daily Redis send counters (best effort)."""
    client = _redis()
    if client is None:
        return
    try:
        pipe = client.pipeline()
        hkey, dkey = _hour_key(user_id), _day_key(user_id)
        pipe.incr(hkey)
        pipe.expire(hkey, _HOUR_TTL_SECONDS)
        pipe.incr(dkey)
        pipe.expire(dkey, _DAY_TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning("failed to increment redis send counters for %s: %s", user_id, exc)


def _release_send_slot(user_id) -> None:
    """Hand back a reserved slot when the send did not happen."""
    client = _redis()
    if client is None:
        return
    try:
        pipe = client.pipeline()
        pipe.decr(_hour_key(user_id))
        pipe.decr(_day_key(user_id))
        pipe.execute()
    except Exception as exc:
        logger.warning("failed to release redis send slot for %s: %s", user_id, exc)


# --------------------------------------------------------------------------- limits


def effective_daily_cap(settings_row: UserSettings) -> int:
    """Daily cap in force right now.

    min(max_emails_per_day, warmup_daily_cap) while warmup is enabled (a celery-
    beat job ramps warmup_daily_cap up over time), otherwise max_emails_per_day.
    """
    max_day = settings_row.max_emails_per_day or 0
    if settings_row.warmup_enabled and settings_row.warmup_daily_cap:
        return min(max_day, settings_row.warmup_daily_cap)
    return max_day


def check_rate_limit(user_id, settings_row: UserSettings) -> tuple[bool, str]:
    """Whether a send is allowed right now for this user (Redis-backed limits).

    Checks in order, first failure short-circuits:
      1. Send window: current UTC hour in [send_start_hour, send_end_hour]
         (inclusive both ends — send_end_hour is capped at 23, so an exclusive
         upper bound would make the 23:00 hour permanently unsendable).
      2. Hourly:  sent this hour < max_emails_per_hour.
      3. Daily:   sent today     < effective_daily_cap().

    Read-only: it never consumes a slot — `send_email` increments the counters
    only after SendGrid accepts the message. Monthly quota is enforced in
    `send_email` (needs the DB). If Redis is down the hourly/daily checks are
    skipped and the send is ALLOWED (dev-friendly).

    Returns (allowed, reason).
    """
    now = datetime.utcnow()
    start = settings_row.send_start_hour if settings_row.send_start_hour is not None else 0
    end = settings_row.send_end_hour if settings_row.send_end_hour is not None else 23
    if not (start <= now.hour <= end):
        return False, f"outside send window {start:02d}:00-{end:02d}:00 UTC (now {now.hour:02d}:00)"

    client = _redis()
    if client is None:
        return True, "redis unavailable — limits skipped (dev mode)"

    try:
        hourly = int(client.get(_hour_key(user_id)) or 0)
        max_hour = settings_row.max_emails_per_hour or 0
        if max_hour and hourly >= max_hour:
            return False, f"hourly cap reached ({hourly}/{max_hour})"

        daily = int(client.get(_day_key(user_id)) or 0)
        cap = effective_daily_cap(settings_row)
        if cap and daily >= cap:
            warm = settings_row.warmup_enabled and cap == (settings_row.warmup_daily_cap or 0)
            label = "warmup daily cap" if warm else "daily cap"
            return False, f"{label} reached ({daily}/{cap})"
    except Exception as exc:  # a mid-flight redis error must not block sending
        logger.warning("redis read failed during rate check for %s: %s", user_id, exc)
        return True, "redis error — limits skipped"

    return True, "ok"


def reserve_send_slot(user_id, settings_row: UserSettings) -> tuple[bool, str]:
    """Atomically claim one send slot. This is the ENFORCING check.

    `check_rate_limit` only reads, so with several workers two of them can both
    see 99/100 and both send — the cap is exceeded by however many workers race.
    Here the counter is INCREMENTed first (atomic in Redis) and the resulting
    value is what's tested, so exactly one caller can be the one that crosses
    the cap. A rejected or failed send calls `_release_send_slot` to hand the
    slot back.

    Fails OPEN when Redis is down, same as the read-only check: a dev machine
    without Redis must still be able to send.
    """
    now = datetime.utcnow()
    start = settings_row.send_start_hour if settings_row.send_start_hour is not None else 0
    end = settings_row.send_end_hour if settings_row.send_end_hour is not None else 23
    if not (start <= now.hour <= end):
        return False, f"outside send window {start:02d}:00-{end:02d}:00 UTC (now {now.hour:02d}:00)"

    client = _redis()
    if client is None:
        return True, "redis unavailable — limits skipped (dev mode)"

    try:
        hkey, dkey = _hour_key(user_id), _day_key(user_id)
        pipe = client.pipeline()
        pipe.incr(hkey)
        pipe.expire(hkey, _HOUR_TTL_SECONDS)
        pipe.incr(dkey)
        pipe.expire(dkey, _DAY_TTL_SECONDS)
        results = pipe.execute()
        hourly, daily = int(results[0]), int(results[2])
    except Exception as exc:
        logger.warning("redis reserve failed for %s: %s", user_id, exc)
        return True, "redis error — limits skipped"

    # Counters now INCLUDE this send, so the comparison is > not >=.
    max_hour = settings_row.max_emails_per_hour or 0
    if max_hour and hourly > max_hour:
        _release_send_slot(user_id)
        return False, f"hourly cap reached ({hourly - 1}/{max_hour})"

    cap = effective_daily_cap(settings_row)
    if cap and daily > cap:
        _release_send_slot(user_id)
        warm = settings_row.warmup_enabled and cap == (settings_row.warmup_daily_cap or 0)
        label = "warmup daily cap" if warm else "daily cap"
        return False, f"{label} reached ({daily - 1}/{cap})"

    return True, "ok"


# --------------------------------------------------------------------------- unsubscribe tokens


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def make_unsub_token(user_id, email: str) -> str:
    """Signed, URL-safe unsubscribe token: '<payload_b64>.<hmac_b64>'.

    itsdangerous-free — HMAC-SHA256 over 'user_id:email' keyed by SECRET_KEY.
    """
    payload = f"{user_id}:{email}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


def verify_unsub_token(token: str) -> tuple[str, str] | None:
    """Verify a token from make_unsub_token. Returns (user_id, email) or None.

    Constant-time signature comparison; any malformed/tampered token -> None.
    """
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
        expected = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        user_id, email = payload.decode().split(":", 1)
        if not user_id or not email:
            return None
        return user_id, email
    except Exception:
        return None


# --------------------------------------------------------------------------- footer / body


def build_footer(settings_row: UserSettings, token: str, base_url: str) -> str:
    """CAN-SPAM footer (HTML) appended to every email, including follow-ups.

    One-click unsubscribe link to {base_url}/api/u/{token} plus the account's
    physical postal address.
    """
    unsub_url = f"{base_url.rstrip('/')}/api/u/{token}"
    address = html.escape(settings_row.physical_address or "")
    return (
        '<div style="margin-top:24px;padding-top:12px;border-top:1px solid #e0e0e0;'
        'font-size:12px;color:#888888;line-height:1.5;">'
        f'<a href="{unsub_url}" style="color:#888888;">Unsubscribe</a>'
        " from these emails."
        f'<br>{address}'
        "</div>"
    )


def _footer_text(settings_row: UserSettings, token: str, base_url: str) -> str:
    """Plain-text version of the footer for the text/plain alternative part."""
    unsub_url = f"{base_url.rstrip('/')}/api/u/{token}"
    address = settings_row.physical_address or ""
    return f"\n\n---\nUnsubscribe: {unsub_url}\n{address}"


def _html_body(email_body: str, footer_html: str) -> str:
    """Escape the plain email body, convert newlines to <br>, append the footer."""
    escaped = html.escape(email_body or "").replace("\n", "<br>")
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222222;">{escaped}</div>{footer_html}'


# --------------------------------------------------------------------------- db helpers


def _is_suppressed(db: Session, user_id, email: str) -> bool:
    """Has this address opted out?

    Matching is CASE-INSENSITIVE and also checks the hashed column. A lead
    re-discovered as `Owner@Acme.com` must still match a suppression stored as
    `owner@acme.com` — an exact-case comparison silently re-mails people who
    unsubscribed (CAN-SPAM violation). Hash matching keeps erased contacts
    (privacy purge) suppressed after their plaintext address is removed.
    """
    norm = normalize_email(email)
    if not norm:
        return False
    return (
        db.query(Suppression)
        .filter(
            Suppression.user_id == user_id,
            or_(
                func.lower(Suppression.email) == norm,
                Suppression.email_hash == hash_email(norm),
            ),
        )
        .first()
        is not None
    )


def _add_suppression(db: Session, user_id, email: str, reason: SuppressionReason) -> None:
    """Insert a suppression row, ignoring the unique (user_id, email) collision."""
    norm = normalize_email(email)
    if not norm or _is_suppressed(db, user_id, norm):
        return
    db.add(
        Suppression(
            user_id=user_id, email=norm, email_hash=hash_email(norm), reason=reason
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # concurrent insert already recorded it


def _bump_emails_sent(db: Session, user_id) -> None:
    """Upsert the usage_counters row for the current 'YYYY-MM', +1 email sent."""
    period = datetime.utcnow().strftime("%Y-%m")
    row = (
        db.query(UsageCounter)
        .filter(UsageCounter.user_id == user_id, UsageCounter.period == period)
        .first()
    )
    if row is None:
        row = UsageCounter(user_id=user_id, period=period, emails_sent=0)
        db.add(row)
    row.emails_sent = (row.emails_sent or 0) + 1
    db.commit()


def _monthly_quota_reached(db: Session, user: User) -> bool:
    """True if this month's emails_sent has hit the user's monthly quota."""
    quota = user.monthly_email_quota or 0
    if quota <= 0:
        return False
    period = datetime.utcnow().strftime("%Y-%m")
    row = (
        db.query(UsageCounter)
        .filter(UsageCounter.user_id == user.id, UsageCounter.period == period)
        .first()
    )
    sent = row.emails_sent if row and row.emails_sent else 0
    return sent >= quota


# --------------------------------------------------------------------------- send


def send_email(
    db: Session,
    lead: Lead,
    user: User,
    settings_row: UserSettings,
    sendgrid_key: str,
    base_url: str,
    *,
    sequence_step: int = 0,
) -> dict:
    """Send one email to `lead` via SendGrid, honoring every guardrail.

    Order of operations:
      1. Suppression check -> lead.status='unsubscribed', skip.
      2. Monthly quota + rate/window/warmup limits -> lead.status='queued', skip.
      3. Build the SendGrid Mail (from settings, HTML body + footer, plain-text
         alternative, List-Unsubscribe[-Post] headers, open tracking).
      4. Send. 2xx -> lead.status='sent', sent_at, EmailLog(sent, message_id),
         usage_counters.emails_sent++, Redis counters++.
      5. 4xx -> lead.status='bounced' + reason (and suppress the address).
         5xx / timeout -> retry up to 3x with exponential backoff, then 'failed'.

    Never raises. Returns {"status": str, "message_id": str | None, ...}.
    """
    try:
        recipient = (lead.email or "").strip()
        if not recipient:
            lead.status = LeadStatus.failed
            db.commit()
            return {"status": "failed", "message_id": None, "reason": "lead has no email address"}

        from_email = (settings_row.from_email or "").strip()
        if not from_email:
            lead.status = LeadStatus.failed
            db.commit()
            return {"status": "failed", "message_id": None, "reason": "no from_email configured"}

        # 1. Suppression --------------------------------------------------------
        if _is_suppressed(db, user.id, recipient):
            lead.status = LeadStatus.unsubscribed
            db.commit()
            return {"status": "unsubscribed", "message_id": None, "reason": "recipient suppressed"}

        # 2. Quota + rate limits ------------------------------------------------
        if _monthly_quota_reached(db, user):
            lead.status = LeadStatus.queued
            db.commit()
            return {"status": "queued", "message_id": None, "reason": "monthly quota reached"}

        # Claims the slot atomically — see reserve_send_slot. Every path below
        # that does NOT put a message on the wire must release it again.
        allowed, reason = reserve_send_slot(user.id, settings_row)
        if not allowed:
            lead.status = LeadStatus.queued
            db.commit()
            return {"status": "queued", "message_id": None, "reason": reason}

        # 3. Build the message --------------------------------------------------
        from sendgrid import SendGridAPIClient  # local imports: keep module import-light
        from sendgrid.helpers.mail import (
            Header,
            Mail,
            OpenTracking,
            TrackingSettings,
        )

        token = make_unsub_token(user.id, recipient)
        unsub_url = f"{base_url.rstrip('/')}/api/u/{token}"
        subject = lead.email_subject or "Quick question"
        html_content = _html_body(lead.email_body or "", build_footer(settings_row, token, base_url))
        plain_content = (lead.email_body or "") + _footer_text(settings_row, token, base_url)

        message = Mail(
            from_email=(from_email, settings_row.from_name or from_email),
            to_emails=recipient,
            subject=subject,
            plain_text_content=plain_content,
            html_content=html_content,
        )
        message.header = Header("List-Unsubscribe", f"<{unsub_url}>")
        message.header = Header("List-Unsubscribe-Post", "List-Unsubscribe=One-Click")

        tracking = TrackingSettings()
        tracking.open_tracking = OpenTracking(enable=True)
        message.tracking_settings = tracking

        client = SendGridAPIClient(sendgrid_key)

        # 4/5. Send with retry on 5xx / network errors --------------------------
        last_reason = "unknown error"
        for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
            try:
                response = client.send(message)
            except Exception as exc:
                # SendGrid raises python_http_client.exceptions.HTTPError for
                # non-2xx; those carry .status_code. A 4xx is a permanent reject
                # (bounce); a 5xx or a bare network/timeout error is retryable.
                status_code = getattr(exc, "status_code", None)
                if status_code is not None and 400 <= status_code < 500:
                    return _handle_bounce(db, lead, user, recipient, subject, sequence_step, exc)
                last_reason = f"{exc.__class__.__name__}: {exc}"
                logger.warning(
                    "send attempt %d/%d failed for lead %s: %s",
                    attempt,
                    _MAX_SEND_ATTEMPTS,
                    lead.id,
                    last_reason,
                )
                if attempt < _MAX_SEND_ATTEMPTS:
                    time.sleep(_BACKOFF_BASE_SECONDS ** attempt)
                continue

            status_code = getattr(response, "status_code", 0)
            if 200 <= status_code < 300:
                return _handle_success(
                    db, lead, user, settings_row, recipient, subject, sequence_step, response
                )
            if 400 <= status_code < 500:
                return _handle_bounce(
                    db, lead, user, recipient, subject, sequence_step,
                    reason=f"SendGrid {status_code}",
                )
            # 5xx from a returned response — retry
            last_reason = f"SendGrid {status_code}"
            logger.warning(
                "send attempt %d/%d got %s for lead %s",
                attempt, _MAX_SEND_ATTEMPTS, status_code, lead.id,
            )
            if attempt < _MAX_SEND_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_SECONDS ** attempt)

        # Retries exhausted — leave recoverable, mark failed for now.
        _release_send_slot(user.id)  # nothing went out; don't burn the quota
        lead.status = LeadStatus.failed
        db.commit()
        return {"status": "failed", "message_id": None, "reason": last_reason}

    except Exception as exc:  # absolute backstop — never raise to the caller
        logger.exception("send_email crashed for lead %s", getattr(lead, "id", "?"))
        try:
            _release_send_slot(user.id)
        except Exception:
            pass
        try:
            db.rollback()
        except Exception:
            pass
        return {"status": "failed", "message_id": None, "reason": f"{exc.__class__.__name__}: {exc}"}


def _handle_success(
    db: Session,
    lead: Lead,
    user: User,
    settings_row: UserSettings,
    recipient: str,
    subject: str,
    sequence_step: int,
    response,
) -> dict:
    """Persist a successful send: lead, EmailLog, usage counter, Redis counters."""
    headers = getattr(response, "headers", {}) or {}
    message_id = None
    if headers:
        try:
            message_id = headers.get("X-Message-Id") or headers.get("x-message-id")
        except AttributeError:  # HTTPMessage-style object
            message_id = headers.get("X-Message-Id")

    now = datetime.utcnow()
    lead.status = LeadStatus.sent
    lead.sent_at = now
    db.add(
        EmailLog(
            user_id=user.id,
            lead_id=lead.id,
            campaign_id=lead.campaign_id,
            message_id=message_id,
            from_email=settings_row.from_email,
            to_email=recipient,
            subject=subject,
            sequence_step=sequence_step,
            status=EmailLogStatus.sent,
            sent_at=now,
        )
    )
    db.commit()

    _bump_emails_sent(db, user.id)
    # NB: the Redis hourly/daily counters were already incremented by
    # reserve_send_slot before the send. Incrementing again here would
    # double-count every message and halve the effective cap.

    logger.info("sent email to %s (lead %s, step %d)", recipient, lead.id, sequence_step)
    return {"status": "sent", "message_id": message_id}


def _handle_bounce(
    db: Session,
    lead: Lead,
    user: User,
    recipient: str,
    subject: str,
    sequence_step: int,
    exc: Exception | None = None,
    reason: str | None = None,
) -> dict:
    """Record a permanent 4xx reject: bounced EmailLog + lead + suppression."""
    if reason is None:
        status_code = getattr(exc, "status_code", None)
        reason = f"SendGrid {status_code}" if status_code else (str(exc) if exc else "rejected")

    now = datetime.utcnow()
    lead.status = LeadStatus.bounced
    db.add(
        EmailLog(
            user_id=user.id,
            lead_id=lead.id,
            campaign_id=lead.campaign_id,
            from_email=None,
            to_email=recipient,
            subject=subject,
            sequence_step=sequence_step,
            status=EmailLogStatus.bounced,
            bounce_reason=reason[:2000],
        )
    )
    db.commit()

    # Hard bounce -> suppress so we never retry this address (PRD §7).
    _add_suppression(db, user.id, recipient, SuppressionReason.bounce)

    # SendGrid refused the message, so nothing reached a mailbox — give the
    # reserved slot back rather than burning a send from the daily cap.
    _release_send_slot(user.id)

    logger.warning("bounced email to %s (lead %s): %s", recipient, lead.id, reason)
    return {"status": "bounced", "message_id": None, "reason": reason}
