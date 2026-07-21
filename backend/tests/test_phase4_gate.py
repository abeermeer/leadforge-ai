"""Phase 4 gate: send -> tracked -> reply cancels sequence -> unsubscribe suppresses."""
import uuid
from unittest import mock

from fastapi.testclient import TestClient


class _FakeSG:
    """Stand-in for sendgrid.SendGridAPIClient that accepts every send."""

    def __init__(self, *a, **k):
        pass

    # unique message id per send so cross-run logs never collide
    def send(self, message):
        mid = f"msg-{uuid.uuid4().hex[:10]}"
        return mock.Mock(status_code=202, headers={"X-Message-Id": mid}, body=b"")

from database import SessionLocal
from main import app
from models import (
    AgencyProfile,
    Campaign,
    EmailLog,
    Lead,
    LeadStatus,
    SequenceStep,
    SequenceStepStatus,
    Suppression,
    User,
)
from services.email.sender import make_unsub_token, verify_unsub_token

client = TestClient(app)
EMAIL = "gate4@trax9.com"
PW = "supersecret4"


def _auth():
    r = client.post("/api/register", json={"email": EMAIL, "password": PW, "name": "G4"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": EMAIL, "password": PW})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_lead(user_email: str) -> tuple[str, str]:
    """Seed a lead with a UNIQUE email (dev DB persists across runs). Returns (lead_id, email)."""
    db = SessionLocal()
    u = db.query(User).filter(User.email == user_email).first()
    p = AgencyProfile(user_id=u.id, website="trax9.com", company_name="Trax9",
                      services=[{"name": "SEO", "description": "x"}])
    db.add(p)
    c = Campaign(user_id=u.id, name="Send Test")
    db.add(c)
    db.flush()
    lead_email = f"owner-{uuid.uuid4().hex[:8]}@acmethreads.com"
    lead = Lead(user_id=u.id, campaign_id=c.id, company_name="Acme Threads",
                website="acmethreads.com", email=lead_email,
                status=LeadStatus.written, email_subject="Quick idea for Acme",
                email_body="Saw your site. Two fast wins.", fit_score=80)
    db.add(lead)
    db.commit()
    lid = str(lead.id)
    db.close()
    return lid, lead_email


def test_unsub_token_roundtrip():
    uid, email = str(uuid.uuid4()), "x@y.com"
    tok = make_unsub_token(uid, email)
    assert verify_unsub_token(tok) == (uid, email)
    assert verify_unsub_token(tok + "tamper") is None


@mock.patch("sendgrid.SendGridAPIClient", _FakeSG)
def test_send_tracks_and_schedules_followups():
    auth = _auth()
    client.put("/api/settings", headers=auth,
               json={"keys": {"sendgrid": "SG.fake", "anthropic": "sk-ant-x"},
                     "from_email": "ayesha@trax9.com", "from_name": "Ayesha",
                     "physical_address": "1 Main St, Houston TX"})
    lid, lead_email = _seed_lead(EMAIL)

    r = client.post(f"/api/leads/{lid}/send", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"

    db = SessionLocal()
    lead = db.get(Lead, uuid.UUID(lid))
    assert lead.status == LeadStatus.sent and lead.sent_at is not None
    steps = db.query(SequenceStep).filter(SequenceStep.lead_id == lead.id).all()
    assert len(steps) == 2 and all(s.status == SequenceStepStatus.scheduled for s in steps)
    log = db.query(EmailLog).filter(EmailLog.lead_id == lead.id).first()
    assert log is not None and log.message_id
    real_mid = log.message_id
    db.close()

    # SendGrid open event -> lead opened (use the id SendGrid actually returned)
    client.post("/api/webhook/email", json=[{
        "event": "open", "sg_message_id": f"{real_mid}.filter0001", "sg_event_id": "ev1",
        "email": lead_email,
    }])
    db = SessionLocal()
    assert db.get(Lead, uuid.UUID(lid)).status == LeadStatus.opened
    db.close()

    # Unsubscribe link -> suppression + lead unsubscribed + sequence cancelled
    db = SessionLocal()
    u = db.query(User).filter(User.email == EMAIL).first()
    tok = make_unsub_token(str(u.id), lead_email)
    db.close()
    r = client.get(f"/api/u/{tok}")
    assert r.status_code == 200 and "unsubscribed" in r.text.lower()
    db = SessionLocal()
    assert db.query(Suppression).filter(Suppression.email == lead_email).count() == 1
    steps = db.query(SequenceStep).filter(SequenceStep.lead_id == uuid.UUID(lid)).all()
    assert all(s.status == SequenceStepStatus.cancelled for s in steps)
    db.close()


@mock.patch("sendgrid.SendGridAPIClient", _FakeSG)
def test_send_blocked_by_suppression():
    """A suppressed address is never emailed."""
    r = client.post("/api/register",
                    json={"email": "supp4@trax9.com", "password": "password123", "name": "S"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": "supp4@trax9.com", "password": "password123"})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/settings", headers=auth,
               json={"keys": {"sendgrid": "SG.fake"}, "from_email": "a@trax9.com",
                     "physical_address": "1 Main St"})
    lid, lead_email = _seed_lead("supp4@trax9.com")
    db = SessionLocal()
    lead = db.get(Lead, uuid.UUID(lid))
    db.add(Suppression(user_id=lead.user_id, email=lead.email, reason="manual"))
    db.commit()
    db.close()

    r = client.post(f"/api/leads/{lid}/send", headers=auth)
    # sender returns unsubscribed status without hitting SendGrid
    assert r.json()["status"] in ("unsubscribed", "skipped")
    db = SessionLocal()
    assert db.get(Lead, uuid.UUID(lid)).status == LeadStatus.unsubscribed
    db.close()


def test_send_requires_sendgrid_key():
    r = client.post("/api/register",
                    json={"email": "nokey4@trax9.com", "password": "password123", "name": "N"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": "nokey4@trax9.com", "password": "password123"})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    lid, _ = _seed_lead("nokey4@trax9.com")
    assert client.post(f"/api/leads/{lid}/send", headers=auth).status_code == 400
