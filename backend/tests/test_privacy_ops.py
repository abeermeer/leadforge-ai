"""Privacy + ops gates (plan phases 3 & 4).

The load-bearing assertions here are the two that protect real people:
suppression must survive case differences and re-import, and erasing a contact
must not silently make them contactable again.
"""
import uuid

from fastapi.testclient import TestClient

from database import SessionLocal
from main import app
from models import (
    Campaign,
    EmailLog,
    EmailLogStatus,
    Lead,
    LeadStatus,
    Suppression,
    SuppressionReason,
    User,
)
from services import metrics, privacy
from services.email.sender import _add_suppression, _is_suppressed

client = TestClient(app)

EMAIL = "privacy@t.co"
PW = "password123"


def _auth():
    r = client.post("/api/register", json={"email": EMAIL, "password": PW, "name": "P"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": EMAIL, "password": PW})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user(db):
    return db.query(User).filter(User.email == EMAIL).first()


# --------------------------------------------------------- 4.3 suppression is permanent


def test_suppression_matches_case_insensitively():
    """A lead re-discovered with different casing must still be suppressed.

    Exact-case matching would re-mail someone who unsubscribed — the CAN-SPAM
    failure this whole record exists to prevent.
    """
    _auth()
    db = SessionLocal()
    u = _user(db)
    addr = f"MiXeD-{uuid.uuid4().hex[:6]}@Example.COM"
    _add_suppression(db, u.id, addr, SuppressionReason.unsubscribe)

    assert _is_suppressed(db, u.id, addr) is True
    assert _is_suppressed(db, u.id, addr.lower()) is True
    assert _is_suppressed(db, u.id, addr.upper()) is True
    assert _is_suppressed(db, u.id, f"  {addr.lower()}  ") is True
    assert _is_suppressed(db, u.id, "someone-else@example.com") is False
    db.close()


def test_suppression_survives_reimport_of_same_lead():
    """Re-importing a suppressed address as a fresh lead must not un-suppress it."""
    _auth()
    db = SessionLocal()
    u = _user(db)
    addr = f"optout-{uuid.uuid4().hex[:6]}@acme.com"
    _add_suppression(db, u.id, addr, SuppressionReason.unsubscribe)

    # Simulate a later discovery run re-adding the same person, different case.
    c = Campaign(user_id=u.id, name="reimport")
    db.add(c)
    db.flush()
    db.add(
        Lead(
            user_id=u.id, campaign_id=c.id, company_name="Acme",
            website=f"acme-{uuid.uuid4().hex[:6]}.com", email=addr.upper(),
            status=LeadStatus.discovered,
        )
    )
    db.commit()

    assert _is_suppressed(db, u.id, addr.upper()) is True
    # And only one suppression row exists — no duplicate on re-add.
    _add_suppression(db, u.id, addr.upper(), SuppressionReason.manual)
    n = db.query(Suppression).filter(
        Suppression.user_id == u.id, Suppression.email == addr.lower()
    ).count()
    assert n == 1
    db.close()


# --------------------------------------------------------- 4.1 erasure


def test_purge_email_erases_data_but_keeps_suppression():
    _auth()
    db = SessionLocal()
    u = _user(db)
    addr = f"erase-{uuid.uuid4().hex[:6]}@acme.com"

    c = Campaign(user_id=u.id, name="erase-me")
    db.add(c)
    db.flush()
    lead = Lead(
        user_id=u.id, campaign_id=c.id, company_name="Acme",
        website=f"erase-{uuid.uuid4().hex[:6]}.com", email=addr,
        status=LeadStatus.sent,
    )
    db.add(lead)
    db.flush()
    db.add(
        EmailLog(
            user_id=u.id, lead_id=lead.id, campaign_id=c.id,
            to_email=addr, status=EmailLogStatus.sent, message_id="m-1",
        )
    )
    _add_suppression(db, u.id, addr, SuppressionReason.unsubscribe)
    db.commit()

    counts = privacy.purge_email(db, addr, user_id=u.id)
    assert counts["leads"] == 1
    assert counts["email_logs"] >= 1
    assert counts["suppressions_anonymized"] == 1

    # Personal data gone…
    assert db.query(Lead).filter(Lead.email == addr).first() is None
    assert db.query(EmailLog).filter(EmailLog.to_email == addr).first() is None
    # …but the person is STILL suppressed, and their address is no longer stored.
    assert _is_suppressed(db, u.id, addr) is True
    assert db.query(Suppression).filter(Suppression.email == addr.lower()).first() is None
    db.close()


def test_purge_endpoint_is_tenant_scoped():
    """One operator's purge must not touch another operator's rows."""
    auth_a = _auth()
    rb = client.post(
        "/api/register", json={"email": "privacyb@t.co", "password": PW, "name": "B"}
    )
    if rb.status_code == 409:
        rb = client.post("/api/login", json={"email": "privacyb@t.co", "password": PW})

    db = SessionLocal()
    ub = db.query(User).filter(User.email == "privacyb@t.co").first()
    shared = f"shared-{uuid.uuid4().hex[:6]}@acme.com"
    cb = Campaign(user_id=ub.id, name="b-camp")
    db.add(cb)
    db.flush()
    db.add(
        Lead(
            user_id=ub.id, campaign_id=cb.id, company_name="B's lead",
            website=f"bcorp-{uuid.uuid4().hex[:6]}.com", email=shared,
            status=LeadStatus.discovered,
        )
    )
    db.commit()
    db.close()

    r = client.post("/api/privacy/purge", headers=auth_a, json={"email": shared})
    assert r.status_code == 200
    assert r.json()["purged"]["leads"] == 0  # A owns none of them

    db = SessionLocal()
    assert db.query(Lead).filter(Lead.email == shared).first() is not None
    db.close()


# --------------------------------------------------------- 4.2 retention


def test_retention_purge_removes_old_rows_but_keeps_suppressions():
    from datetime import datetime, timedelta

    _auth()
    db = SessionLocal()
    u = _user(db)
    c = Campaign(user_id=u.id, name="old")
    db.add(c)
    db.flush()
    old = Lead(
        user_id=u.id, campaign_id=c.id, company_name="Ancient",
        website=f"ancient-{uuid.uuid4().hex[:6]}.com",
        email=f"old-{uuid.uuid4().hex[:6]}@x.com", status=LeadStatus.sent,
    )
    old.created_at = datetime.utcnow() - timedelta(days=400)
    db.add(old)
    _add_suppression(db, u.id, f"keep-{uuid.uuid4().hex[:6]}@x.com", SuppressionReason.unsubscribe)
    db.commit()
    old_id = old.id
    supp_before = db.query(Suppression).filter(Suppression.user_id == u.id).count()

    privacy.purge_expired_data(db, months=12)

    assert db.get(Lead, old_id) is None
    assert db.query(Suppression).filter(Suppression.user_id == u.id).count() == supp_before
    db.close()


# --------------------------------------------------------- 3.1 metrics


def test_metrics_summary_counts_and_bounce_rate():
    auth = _auth()
    db = SessionLocal()
    u = _user(db)
    c = Campaign(user_id=u.id, name=f"metrics-{uuid.uuid4().hex[:6]}")
    db.add(c)
    db.flush()
    lead = Lead(
        user_id=u.id, campaign_id=c.id, company_name="M",
        website=f"m-{uuid.uuid4().hex[:6]}.com", status=LeadStatus.sent,
    )
    db.add(lead)
    db.flush()
    for status in (
        EmailLogStatus.sent,
        EmailLogStatus.sent,
        EmailLogStatus.sent,
        EmailLogStatus.bounced,
    ):
        db.add(
            EmailLog(
                user_id=u.id, lead_id=lead.id, campaign_id=c.id,
                to_email="m@x.com", status=status,
            )
        )
    db.commit()

    out = metrics.summary(db, u.id, days=30)
    assert out["totals"]["sent"] >= 4
    assert out["totals"]["bounced"] >= 1
    assert out["totals"]["delivered"] == out["totals"]["sent"] - out["totals"]["bounced"]
    assert out["totals"]["bounce_rate"] > 0
    assert any(row["date"] for row in out["series"])
    db.close()

    r = client.get("/api/metrics/summary?days=7", headers=auth)
    assert r.status_code == 200
    assert "totals" in r.json() and "campaigns" in r.json()


def test_metrics_requires_auth():
    client.cookies.clear()
    assert client.get("/api/metrics/summary").status_code == 401


# --------------------------------------------------------- aggregate dashboard


def test_dashboard_aggregates_in_one_call():
    """One request must carry everything the Dashboard renders.

    Replaces a 1 + 2N fan-out (campaign list, then leads + tasks per campaign)
    that ran on a 5s poll.
    """
    auth = _auth()
    db = SessionLocal()
    u = _user(db)
    c = Campaign(user_id=u.id, name=f"dash-{uuid.uuid4().hex[:6]}")
    db.add(c)
    db.flush()
    for i in range(3):
        db.add(
            Lead(
                user_id=u.id, campaign_id=c.id, company_name=f"D{i}",
                website=f"d{i}-{uuid.uuid4().hex[:6]}.com", status=LeadStatus.discovered,
            )
        )
    db.commit()
    db.close()

    r = client.get("/api/dashboard?days=30", headers=auth)
    assert r.status_code == 200
    body = r.json()

    for key in ("campaigns", "total_leads", "status_counts", "series", "recent_leads", "tasks"):
        assert key in body, f"missing {key}"

    assert body["campaigns"] >= 1
    assert body["total_leads"] >= 3
    assert body["status_counts"].get("discovered", 0) >= 3
    # Series covers the whole window and its counts reconcile with the total.
    assert len(body["series"]) == 30
    assert sum(row["leads"] for row in body["series"]) <= body["total_leads"]
    # Recent leads carry the campaign name the table shows (no N+1 on the client).
    if body["recent_leads"]:
        assert "campaign_name" in body["recent_leads"][0]


def test_dashboard_is_tenant_scoped():
    """One operator's dashboard must not count another operator's leads."""
    rb = client.post(
        "/api/register", json={"email": "dashb@t.co", "password": PW, "name": "DB"}
    )
    if rb.status_code == 409:
        rb = client.post("/api/login", json={"email": "dashb@t.co", "password": PW})
    auth_b = {"Authorization": f"Bearer {rb.json()['access_token']}"}

    body = client.get("/api/dashboard", headers=auth_b).json()
    # Fresh operator sees only their own (empty) world.
    assert body["total_leads"] == 0
    assert body["campaigns"] == 0


def test_dashboard_requires_auth():
    client.cookies.clear()
    assert client.get("/api/dashboard").status_code == 401


# --------------------------------------------------------- atomic send-slot reservation


def test_reserve_send_slot_is_atomic_under_concurrency(monkeypatch):
    """Concurrent reservations must never exceed the cap.

    The old read-then-send flow let two workers both observe 99/100 and both
    send. This drives the reserve path against a fake Redis whose INCR is
    atomic, and asserts exactly `cap` reservations succeed out of many.
    """
    from services.email import sender

    class FakeRedis:
        """Minimal Redis with genuinely atomic INCR/DECR under a lock."""

        def __init__(self):
            import threading

            self.store = {}
            self.lock = threading.Lock()

        def pipeline(self):
            return FakePipe(self)

    class FakePipe:
        def __init__(self, r):
            self.r = r
            self.ops = []

        def incr(self, key):
            self.ops.append(("incr", key))
            return self

        def decr(self, key):
            self.ops.append(("decr", key))
            return self

        def expire(self, key, ttl):
            self.ops.append(("expire", key))
            return self

        def execute(self):
            out = []
            with self.r.lock:
                for op, key in self.ops:
                    if op == "incr":
                        self.r.store[key] = self.r.store.get(key, 0) + 1
                        out.append(self.r.store[key])
                    elif op == "decr":
                        self.r.store[key] = self.r.store.get(key, 0) - 1
                        out.append(self.r.store[key])
                    else:
                        out.append(True)
            return out

    fake = FakeRedis()
    monkeypatch.setattr(sender, "_redis", lambda: fake)

    class Row:
        max_emails_per_hour = 5
        max_emails_per_day = 5
        warmup_enabled = False
        warmup_daily_cap = 0
        send_start_hour = 0
        send_end_hour = 23

    uid = uuid.uuid4()
    results = []

    import threading

    def attempt():
        ok, _ = sender.reserve_send_slot(uid, Row())
        results.append(ok)

    threads = [threading.Thread(target=attempt) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    granted = sum(1 for ok in results if ok)
    assert granted == 5, f"cap is 5 but {granted} sends were allowed"


def test_release_send_slot_returns_quota():
    """A reserved-but-unsent slot must be handed back, not burned."""
    from services.email import sender

    store = {}

    class Pipe:
        def __init__(self):
            self.ops = []

        def incr(self, k):
            self.ops.append(("incr", k))
            return self

        def decr(self, k):
            self.ops.append(("decr", k))
            return self

        def expire(self, k, ttl):
            self.ops.append(("exp", k))
            return self

        def execute(self):
            out = []
            for op, k in self.ops:
                if op == "incr":
                    store[k] = store.get(k, 0) + 1
                    out.append(store[k])
                elif op == "decr":
                    store[k] = store.get(k, 0) - 1
                    out.append(store[k])
                else:
                    out.append(True)
            return out

    class R:
        def pipeline(self):
            return Pipe()

    import pytest as _pytest

    monkey = _pytest.MonkeyPatch()
    monkey.setattr(sender, "_redis", lambda: R())
    try:
        class Row:
            max_emails_per_hour = 10
            max_emails_per_day = 10
            warmup_enabled = False
            warmup_daily_cap = 0
            send_start_hour = 0
            send_end_hour = 23

        uid = uuid.uuid4()
        ok, _ = sender.reserve_send_slot(uid, Row())
        assert ok
        after_reserve = dict(store)
        sender._release_send_slot(uid)
        # Every counter is back to its pre-reservation value.
        assert all(store[k] == after_reserve[k] - 1 for k in after_reserve)
    finally:
        monkey.undo()


# --------------------------------------------------------- 3.4 global handler -> Sentry


def test_unhandled_exception_logs_error_and_returns_request_id(monkeypatch, caplog):
    """The global handler must log at ERROR — that is the exact hook Sentry's
    LoggingIntegration(event_level=ERROR) captures. If this stops logging at
    ERROR, unhandled exceptions stop reaching Sentry silently.
    """
    import logging

    auth = _auth()

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(metrics, "summary", boom)

    raw = TestClient(app, raise_server_exceptions=False)
    raw.cookies.clear()
    with caplog.at_level(logging.ERROR, logger="trax9"):
        r = raw.get("/api/metrics/summary", headers=auth)

    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error"
    assert body["request_id"]  # correlates the user's report to the log line
    assert "synthetic failure" not in str(body)  # never leak internals
    assert any(rec.levelno >= logging.ERROR for rec in caplog.records)
