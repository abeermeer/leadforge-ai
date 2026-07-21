"""Phase 3 gate: audit -> audit_data + fit_score populated; social gated + graceful.

Externals mocked with respx. Playwright audits (meta/google ads) run keyless and
return their empty shapes without a browser, which is the intended offline path.
"""
import json

import respx
from fastapi.testclient import TestClient
from httpx import Response

from main import app
from services.audit.scoring import score_lead

client = TestClient(app)

EMAIL = "gate3@trax9.com"
PASSWORD = "supersecret3"

BRAND_JSON = {
    "industry": "ecommerce - fashion",
    "target_audience": "young women",
    "brand_positioning": "affordable trendy",
    "website_quality_score": 4,
    "strengths": ["clean logo", "active IG", "fast checkout"],
    "weaknesses": ["no schema", "slow mobile", "thin content"],
    "estimated_size": "small",
    "pain_points": ["poor SEO", "no blog"],
    "best_services": ["SEO", "CRO"],
    "competitor_notes": "none",
}


def _auth() -> dict:
    r = client.post(
        "/api/register", json={"email": EMAIL, "password": PASSWORD, "name": "Gate3"}
    )
    if r.status_code == 409:
        r = client.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_score_lead_pure():
    """Deterministic scoring: weak site + running ads + has email scores high."""
    audit = {
        "website": {"tech_stack": ["shopify"], "schema_types": [], "ssl_valid": True},
        "seo": {"onpage_score": 30, "pagespeed": {"mobile": {"performance_score": 25}}},
        "meta_ads": {"has_ads": True, "total_active_ads": 6},
        "google_ads": {"is_advertiser": False},
        "brand": {"industry": "ecommerce - fashion", "estimated_size": "small"},
    }
    lead = {"email": "info@acme.com", "website": "acme.com"}
    ideal = {"industries": ["ecommerce - fashion"]}
    out = score_lead(lead, audit, ideal)
    assert 0 <= out["fit_score"] <= 100
    assert out["fit_score"] > 55  # weak site + ads + email + industry match = hot
    assert isinstance(out["score_reasons"], list) and out["score_reasons"]


@respx.mock
def test_audit_inline_populates_audit_data_and_score():
    auth = _auth()
    # AI key required for the audit endpoint
    client.put(
        "/api/settings",
        headers=auth,
        json={
            "keys": {
                "anthropic": "sk-ant-fake-777",
                "google_places": "places-fake-777",
                "google_custom_search": "cse-fake-777",
                "google_custom_search_cx": "cx-fake-777",
            },
            "ai_provider": "anthropic",
        },
    )

    # Campaign + one lead created via inline discovery
    r = client.post(
        "/api/campaigns",
        headers=auth,
        json={"name": "AuditCo", "seed_keywords": ["shop"], "target_locations": ["Houston"]},
    )
    camp_id = r.json()["id"]

    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "displayName": {"text": "Acme Threads"},
                        "websiteUri": "https://acmethreads.com/",
                        "types": ["clothing_store"],
                        "formattedAddress": "Houston, TX",
                    }
                ]
            },
        )
    )
    respx.get(url__regex=r"https://www\.googleapis\.com/customsearch/v1.*").mock(
        return_value=Response(200, json={"items": []})
    )
    client.post(f"/api/campaigns/{camp_id}/discover", headers=auth)

    # Mock the audit externals: website fetch (any acmethreads url), brand AI
    respx.get(url__regex=r"https?://acmethreads\.com.*").mock(
        return_value=Response(
            200,
            text="<html lang='en'><head><title>Acme Threads Store Online</title>"
            "<meta name='description' content='"
            + ("x" * 140)
            + "'></head><body><h1>Shop</h1><img src='a.png'></body></html>",
        )
    )
    # robots/sitemap probes -> 404 fine
    respx.get(url__regex=r"https?://acmethreads\.com/(robots\.txt|sitemap.*)").mock(
        return_value=Response(404)
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(BRAND_JSON)}],
                "usage": {"input_tokens": 400, "output_tokens": 200},
            },
        )
    )

    r = client.post(f"/api/campaigns/{camp_id}/audit", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "inline"
    assert r.json()["audited"] >= 1

    # Lead now has audit_data + a fit_score, status scored/enriched
    r = client.get(f"/api/campaigns/{camp_id}/leads", headers=auth)
    lead = r.json()["items"][0]
    assert lead["fit_score"] is not None
    assert lead["status"] in ("scored", "enriched")

    detail = client.get(f"/api/leads/{lead['id']}", headers=auth).json()
    assert detail["audit_data"] is not None
    assert "website" in detail["audit_data"]
    assert detail["audit_data"]["brand"]["industry"] == "ecommerce - fashion"
    # No SocialCrawl key configured -> social enrichment skipped, lead still flows
    assert "social" not in detail["audit_data"] or detail["audit_data"].get("social", {}).get(
        "skipped"
    )
    assert detail["score_reasons"]


def test_audit_requires_ai_key():
    # Fresh tenant with no keys
    r = client.post(
        "/api/register",
        json={"email": "nokey3@trax9.com", "password": "password123", "name": "NoKey"},
    )
    if r.status_code == 409:
        r = client.post(
            "/api/login", json={"email": "nokey3@trax9.com", "password": "password123"}
        )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.post(
        "/api/campaigns",
        headers=auth,
        json={"name": "NK", "seed_keywords": ["x"], "target_locations": ["Y"]},
    )
    camp_id = r.json()["id"]
    r = client.post(f"/api/campaigns/{camp_id}/audit", headers=auth)
    assert r.status_code == 400
    assert "AI API key" in r.json()["detail"]
