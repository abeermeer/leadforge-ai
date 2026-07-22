"""Phase 1 gate: health, auth roundtrip, encrypted settings."""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

EMAIL = "gate@trax9.com"
PASSWORD = "supersecret1"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] == "ok"


def test_register_login_me():
    r = client.post(
        "/api/register",
        json={"email": EMAIL, "password": PASSWORD, "name": "Gate Tester"},
    )
    assert r.status_code in (201, 409)  # 409 if re-run against same dev DB

    r = client.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == EMAIL


def test_me_requires_auth():
    # A prior test may have left an auth cookie on the shared client; a genuine
    # no-credentials request must clear it first.
    client.cookies.clear()
    assert client.get("/api/me").status_code == 401


def test_settings_encrypt_and_mask():
    token = client.post("/api/login", json={"email": EMAIL, "password": PASSWORD}).json()[
        "access_token"
    ]
    auth = {"Authorization": f"Bearer {token}"}

    r = client.put(
        "/api/settings",
        headers=auth,
        json={
            "keys": {"sendgrid": "SG.fake-key-abcdef123456", "anthropic": "sk-ant-fake987654"},
            "from_email": "ayesha@trax9.com",
            "warmup_enabled": True,
        },
    )
    assert r.status_code == 200
    body = r.json()

    # Masked on read — never the full key
    assert body["keys_masked"]["sendgrid"].startswith("SG.f")
    assert "..." in body["keys_masked"]["sendgrid"]
    assert "abcdef123456" not in str(body)
    assert body["from_email"] == "ayesha@trax9.com"

    # Stored encrypted at rest — read from whichever sqlite file the app is
    # actually configured to use (trax9_dev.db locally, ci.db / test DB in CI).
    import sqlite3

    from config import settings

    db_path = settings.DATABASE_URL.split("///", 1)[-1] if "sqlite" in settings.DATABASE_URL else "trax9_dev.db"
    con = sqlite3.connect(db_path)
    blob = con.execute("SELECT encrypted_keys FROM user_settings").fetchone()[0]
    assert blob is not None
    assert b"SG.fake" not in blob  # ciphertext, not plaintext

    # Partial update keeps other keys
    r = client.put("/api/settings", headers=auth, json={"keys": {"hunter": "hk-11112222"}})
    masked = r.json()["keys_masked"]
    assert "sendgrid" in masked and "hunter" in masked
