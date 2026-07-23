"""Security regression tests (audit §6): SSRF, webhook auth, IDOR, secrets fail-fast."""
import asyncio
import importlib
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from services.net.safe_http import UnsafeURLError, validate_url

client = TestClient(app)


# ------------------------------------------------------------------ SSRF (1.1)

BAD_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://127.0.0.1:6379/",                     # localhost redis
    "http://localhost/",                           # localhost name
    "http://10.0.0.5/",                            # private
    "http://192.168.1.1/",                         # private
    "http://[::1]/",                               # ipv6 loopback
    "file:///etc/passwd",                          # bad scheme
    "gopher://evil/",                              # bad scheme
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_ssrf_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        asyncio.run(validate_url(url))


def test_ssrf_public_url_allowed():
    # example.com resolves to a public IP — must pass.
    asyncio.run(validate_url("https://example.com/"))


def test_profile_analyze_rejects_private_url():
    r = client.post("/api/register", json={"email": "ssrf@t.co", "password": "password123", "name": "S"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": "ssrf@t.co", "password": "password123"})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.put("/api/settings", headers=auth, json={"keys": {"anthropic": "sk-x"}, "ai_provider": "anthropic"})
    r = client.post("/api/profile/analyze", headers=auth, json={"website": "http://169.254.169.254/"})
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"].lower()


# ------------------------------------------------------------------ webhooks (1.2)

def test_inbound_webhook_requires_secret():
    # No INBOUND_WEBHOOK_SECRET configured -> any secret path is 404 (not triggerable).
    r = client.post("/api/webhook/inbound/anything", data={"from": "x@y.com"})
    assert r.status_code == 404


def test_event_webhook_accepts_when_unsigned_in_dev():
    # No verification key configured in tests -> accepted (dev path), returns 200.
    r = client.post("/api/webhook/email", json=[{"event": "open", "email": "nobody@nowhere.com"}])
    assert r.status_code == 200


# ------------------------------------------------------------------ auth (§3, 1.5)

def test_malformed_token_is_401_not_500():
    r = client.get("/api/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_token_revocation_via_version_bump():
    r = client.post("/api/register", json={"email": "rev@t.co", "password": "password123", "name": "R"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": "rev@t.co", "password": "password123"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/me", headers=auth).status_code == 200

    # Bump the user's token_version -> the old token is now rejected.
    from database import SessionLocal
    from models import User

    db = SessionLocal()
    u = db.query(User).filter(User.email == "rev@t.co").first()
    u.token_version = (u.token_version or 0) + 1
    db.commit()
    db.close()
    assert client.get("/api/me", headers=auth).status_code == 401


# ------------------------------------------------------------------ IDOR (§6)

def _mk_user(email):
    r = client.post("/api/register", json={"email": email, "password": "password123", "name": "U"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_idor_foreign_campaign_is_404():
    a = _mk_user("idora@t.co")
    b = _mk_user("idorb@t.co")
    camp = client.post("/api/campaigns", headers=a, json={"name": "A's"}).json()["id"]
    # B cannot read, update, or delete A's campaign.
    assert client.get(f"/api/campaigns/{camp}", headers=b).status_code == 404
    assert client.put(f"/api/campaigns/{camp}", headers=b, json={"name": "hijack"}).status_code == 404
    assert client.delete(f"/api/campaigns/{camp}", headers=b).status_code == 404
    # A still owns it.
    assert client.get(f"/api/campaigns/{camp}", headers=a).status_code == 200


def test_idor_foreign_lead_is_404():
    a = _mk_user("leada@t.co")
    b = _mk_user("leadb@t.co")
    from database import SessionLocal
    from models import Campaign, Lead, LeadStatus, User

    db = SessionLocal()
    ua = db.query(User).filter(User.email == "leada@t.co").first()
    c = Campaign(user_id=ua.id, name="c")
    db.add(c)
    db.flush()
    lead = Lead(user_id=ua.id, campaign_id=c.id, company_name="X", website="x.com", status=LeadStatus.discovered)
    db.add(lead)
    db.commit()
    lid = str(lead.id)
    db.close()
    assert client.get(f"/api/leads/{lid}", headers=b).status_code == 404
    assert client.delete(f"/api/leads/{lid}", headers=b).status_code == 404
    assert client.get(f"/api/leads/{lid}", headers=a).status_code == 200


# ------------------------------------------------------------------ secrets fail-fast (1.3)

def test_prod_boot_refuses_default_secret(monkeypatch):
    """A non-DEBUG app with the placeholder SECRET_KEY / empty FERNET_KEY must refuse to boot."""
    import config as config_module

    monkeypatch.setattr(config_module.settings, "DEBUG", False)
    monkeypatch.setattr(config_module.settings, "SECRET_KEY", "change-me-jwt-secret")
    monkeypatch.setattr(config_module.settings, "FERNET_KEY", "")

    import main as main_module

    with pytest.raises(RuntimeError):
        main_module._assert_production_secrets()


def test_prod_boot_requires_webhook_verification_key(monkeypatch):
    """Real secrets but no webhook key must still refuse to boot: the webhook
    fails closed, so an unset key silently drops every open/bounce/unsubscribe."""
    import config as config_module

    monkeypatch.setattr(config_module.settings, "DEBUG", False)
    monkeypatch.setattr(config_module.settings, "SECRET_KEY", "a-real-non-default-secret")
    monkeypatch.setattr(
        config_module.settings, "FERNET_KEY", "NnqnTIDsmStD-diXZmHUQAVF3SUa90nm5zOLQC79xyA="
    )
    monkeypatch.setattr(config_module.settings, "SENDGRID_WEBHOOK_VERIFICATION_KEY", "")

    import main as main_module

    with pytest.raises(RuntimeError, match="SENDGRID_WEBHOOK_VERIFICATION_KEY"):
        main_module._assert_production_secrets()


# ------------------------------------------------------------------ SEC-B (cookie auth, verify gate, GDPR)

def test_login_sets_httponly_cookie():
    """Login must plant the httpOnly session cookie so browsers can auth without a header."""
    email = "cookie@t.co"
    r = client.post("/api/register", json={"email": email, "password": "password123", "name": "C"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": email, "password": "password123"})
    # httpx exposes Set-Cookie via the response cookies jar.
    assert "lf_access" in r.cookies
    # And the raw header carries HttpOnly (not visible to JS).
    setc = r.headers.get("set-cookie", "")
    assert "httponly" in setc.lower()


def test_cookie_authenticates_without_bearer():
    """A fresh client that only holds the cookie (no Authorization header) is authenticated."""
    from fastapi.testclient import TestClient as _TC

    c = _TC(app)
    email = "cookieauth@t.co"
    r = c.post("/api/register", json={"email": email, "password": "password123", "name": "C"})
    if r.status_code == 409:
        r = c.post("/api/login", json={"email": email, "password": "password123"})
    # The TestClient now carries lf_access; /api/me with NO bearer must still 200.
    assert c.get("/api/me").status_code == 200


def test_logout_clears_cookie():
    from fastapi.testclient import TestClient as _TC

    c = _TC(app)
    email = "logout@t.co"
    r = c.post("/api/register", json={"email": email, "password": "password123", "name": "L"})
    if r.status_code == 409:
        r = c.post("/api/login", json={"email": email, "password": "password123"})
    assert c.get("/api/me").status_code == 200
    c.post("/api/logout")
    # Cookie dropped -> unauthenticated.
    assert c.get("/api/me").status_code == 401


def test_logout_everywhere_revokes_existing_tokens():
    """Bumping token_version via /logout-everywhere invalidates a previously-minted bearer."""
    email = "logoutall@t.co"
    r = client.post("/api/register", json={"email": email, "password": "password123", "name": "L"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": email, "password": "password123"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/me", headers=auth).status_code == 200
    assert client.post("/api/logout-everywhere", headers=auth).status_code == 200
    # Old bearer no longer valid.
    assert client.get("/api/me", headers=auth).status_code == 401


def test_email_verification_flow():
    """request-verification returns a link; hitting /verify/{token} flips email_verified."""
    email = "verify@t.co"
    r = client.post("/api/register", json={"email": email, "password": "password123", "name": "V"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": email, "password": "password123"})
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    body = client.post("/api/request-verification", headers=auth).json()
    # Fresh user -> a link is issued; on a reused test DB the user may already be
    # verified (idempotent), in which case there's no link and we just assert state.
    if "verification_link" in body:
        token = body["verification_link"].rstrip("/").split("/")[-1]
        assert client.get(f"/api/verify/{token}").status_code == 200

    from database import SessionLocal
    from models import User

    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    verified = bool(u.email_verified)
    db.close()
    assert verified is True


def test_delete_account_purges_user(monkeypatch):
    """GDPR erase: DELETE /account removes the user and their owned rows."""
    email = "gdpr@t.co"
    r = client.post("/api/register", json={"email": email, "password": "password123", "name": "G"})
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": email, "password": "password123"})
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Seed a campaign so cascade delete has something to purge.
    client.post("/api/campaigns", headers=auth, json={"name": "to-erase"})
    assert client.request("DELETE", "/api/account", headers=auth).status_code in (200, 204)

    from database import SessionLocal
    from models import User

    db = SessionLocal()
    gone = db.query(User).filter(User.email == email).first()
    db.close()
    assert gone is None
    # The revoked token can no longer authenticate.
    assert client.get("/api/me", headers=auth).status_code == 401
