# Trax9 AI Lead Generation & Outreach System — Full PRD v2

> One-shot build spec for Claude. Build everything below in the order specified.
> v2 adds: Seller Profiling brain, Email Finding, Reply Tracking, Google Ads Transparency,
> Compliance/Unsubscribe, Multi-tenancy, Secrets Encryption, Proxy Rotation, Lead Scoring,
> Follow-up Sequences, Warmup State Machine, Observability, Tests, Migrations.

---

## System Overview

An AI-powered SaaS platform for Trax9 that:
0. **Understands the agency itself** — paste `trax9.com`, AI extracts what services it sells and who its ideal clients are
1. Scans the internet for businesses matching that ideal-client profile (Google Maps, Google Search, directories)
2. Finds contact email addresses for each lead
3. Audits each lead's website, SEO, Meta ads, and Google ads
4. Runs AI brand R&D and scores each lead for fit
5. Generates personalized cold outreach emails that match a Trax9 service to a real gap found in the audit
6. Sends emails automatically with warmup, rate limits, unsubscribe, and open/reply tracking
7. Runs follow-up sequences
8. Tracks everything in a multi-tenant web dashboard

**Stack**: Python (FastAPI) backend, React (Vite + Tailwind) frontend, PostgreSQL, Celery + Redis, SendGrid, OpenAI/Anthropic API, Google APIs, Playwright.

**Brand colors**: `#1a2744` (primary navy), `#f5a623` (accent gold)

**Design principles (apply everywhere)**:
- Every table is scoped by `user_id` (multi-tenant). No query returns another user's data.
- All third-party secrets are encrypted at rest.
- Every external network call (API or scrape) has timeout + retry + failure state.
- All scraping goes through a rotating proxy + user-agent pool.
- Every long task reports per-item progress.
- There is a hard cost/quota ceiling per user.

---

## Contents
- 1. Data Model (PostgreSQL)
- 2. Backend Architecture (FastAPI)
- 3. Module Specifications
  - 3.0 Seller Profiling (Agency Brain) — NEW
  - 3A. Lead Discovery Engine
  - 3A.5 Email Finder — NEW
  - 3B. Audit Engine (Website, SEO, Meta Ads, Google Ads, Brand R&D)
  - 3B.5 Lead Scoring — NEW
  - 3C. AI Email Writer
  - 3D. Email Sender (+ Warmup, Unsubscribe, Redis rate limits)
  - 3D.5 Reply Tracker — NEW
  - 3E. Follow-up Sequencer — NEW
  - 3F. Web Dashboard
- 4. Frontend Spec (React)
- 5. Task Queue (Celery + Redis)
- 6. Configuration
- 7. Security, Compliance & Anti-Ban — NEW
- 8. Observability, Health & Tests — NEW
- 9. Deployment
- 10. Google APIs Setup
- 11. AI Prompts
- 12. Complete Tool & API Catalog
- 13. Build Order

---

## 1. DATA MODEL (PostgreSQL SQLAlchemy)

```sql
-- Users
users: id (UUID, PK), email (unique), password_hash, name,
       plan (enum: free/pro), monthly_email_quota (INT default 1000),
       created_at

-- Per-user encrypted settings (API keys, sending config)
user_settings: id (UUID, PK), user_id (FK unique),
       encrypted_keys (BYTEA),         -- Fernet-encrypted JSON blob of all API keys
       from_email, from_name,
       max_emails_per_hour (INT), max_emails_per_day (INT),
       send_start_hour (INT), send_end_hour (INT),
       warmup_enabled (BOOL), warmup_daily_cap (INT),  -- current warmup ceiling
       ai_provider (enum: openai/anthropic),
       updated_at

-- Agency profiles (the "brain" — what Trax9 does)
agency_profiles: id (UUID, PK), user_id (FK),
       website (TEXT), company_name,
       services (JSONB),               -- [{name, description}]
       ideal_client (JSONB),           -- {industries[], company_size, geos[], signals[]}
       suggested_keywords (TEXT[]),    -- auto-generated seed keywords
       suggested_locations (TEXT[]),
       positioning (TEXT), raw_analysis (JSONB),
       created_at, updated_at

-- Campaigns
campaigns: id (UUID, PK), user_id (FK), agency_profile_id (FK, nullable),
       name, seed_keywords (TEXT[]), target_locations (TEXT[]),
       industry_filters (TEXT[]),
       status (enum: draft/running/completed/paused),
       created_at, updated_at

-- Leads
leads: id (UUID, PK), user_id (FK), campaign_id (FK),
       company_name, website (unique per campaign),
       phone, email, email_source (enum: scraped/pattern/hunter/manual),
       email_confidence (INT 0-100),
       address, city, country, category, source,
       status (enum: discovered/finding_email/auditing/audited/scored/
               enriching/enriched/writing/written/queued/sending/sent/
               opened/replied/bounced/unsubscribed/failed),
       fit_score (INT 0-100), score_reasons (JSONB),
       audit_data (JSONB), email_subject (TEXT), email_body (TEXT),
       sent_at, opened_at, replied_at, created_at, updated_at

-- Domain-level audit cache (avoid re-auditing same site across campaigns)
audit_cache: id (UUID, PK), domain (unique), audit_data (JSONB),
       fetched_at

-- Email logs
email_logs: id (UUID, PK), user_id (FK), lead_id (FK), campaign_id (FK),
       message_id, sg_event_ids (TEXT[]),   -- for dedup
       from_email, to_email, subject, sequence_step (INT default 0),
       status (enum: queued/sent/bounced/opened/clicked/replied/dropped/spam),
       sent_at, opened_at, replied_at, bounce_reason (TEXT)

-- Follow-up sequence steps
sequence_steps: id (UUID, PK), lead_id (FK), user_id (FK),
       step_number (INT), scheduled_for (TIMESTAMP),
       subject, body, status (enum: scheduled/sent/skipped/cancelled),
       sent_at

-- Suppression list (unsubscribes, spam reports, hard bounces) — CAN-SPAM
suppressions: id (UUID, PK), user_id (FK), email (TEXT),
       reason (enum: unsubscribe/spam/bounce/manual), created_at
       -- UNIQUE(user_id, email)

-- Tasks (UI progress)
tasks: id (UUID, PK), user_id (FK), campaign_id (FK),
       type (enum: profile/discovery/email_find/audit/score/write/send),
       status (enum: pending/running/completed/failed),
       total_items (INT), completed_items (INT), failed_items (INT),
       error (TEXT), created_at, updated_at

-- Usage counters (cost guardrails)
usage_counters: id (UUID, PK), user_id (FK), period (TEXT 'YYYY-MM'),
       emails_sent (INT), ai_tokens (BIGINT), places_calls (INT),
       socialcrawl_credits (INT),
       UNIQUE(user_id, period)
```

**SQLAlchemy models file**: `backend/models.py`. Add index on `leads(user_id, status)`, `leads(campaign_id)`, `email_logs(message_id)`, `suppressions(user_id, email)`.

---

## 2. BACKEND ARCHITECTURE

```
backend/
├── main.py                    # FastAPI app entry, CORS, lifespan, exception handlers
├── config.py                  # Global env defaults (per-user keys live in user_settings)
├── database.py                # SQLAlchemy engine, session, Base
├── models.py                  # ORM models (all tables above)
├── schemas.py                 # Pydantic request/response schemas
├── deps.py                    # get_current_user, get_db, get_user_settings (decrypts keys)
├── crypto.py                  # Fernet encrypt/decrypt for secrets
├── routers/
│   ├── auth.py                # register, login, me
│   ├── profile.py             # analyze agency website, get/save profile
│   ├── campaigns.py           # CRUD + trigger actions
│   ├── leads.py               # detail, send, regenerate, export
│   ├── settings.py            # get/update user_settings (encrypted keys)
│   ├── analytics.py           # dashboard stats + charts
│   └── webhooks.py            # SendGrid events + inbound reply parse
├── services/
│   ├── auth_service.py        # JWT + bcrypt
│   ├── profile/
│   │   └── agency_analyzer.py # NEW — scrape+analyze the agency site
│   ├── discovery/
│   │   ├── google_maps.py
│   │   ├── google_search.py
│   │   ├── directory_scraper.py
│   │   └── email_finder.py    # NEW — find lead email addresses
│   ├── audit/
│   │   ├── website.py
│   │   ├── seo.py
│   │   ├── meta_ads.py
│   │   ├── google_ads.py      # NEW — Google Ads Transparency
│   │   ├── brand_rnd.py
│   │   ├── scoring.py         # NEW — fit score
│   │   └── social.py          # NEW — SocialCrawl enrichment (gated on fit_score)
│   ├── email/
│   │   ├── writer.py
│   │   ├── sender.py          # + warmup, unsubscribe footer, Redis limits
│   │   ├── reply_tracker.py   # NEW — IMAP / inbound parse
│   │   └── sequencer.py       # NEW — follow-up scheduling
│   ├── scraping/
│   │   ├── browser.py         # NEW — Playwright launcher w/ proxy + UA rotation
│   │   └── proxies.py         # NEW — proxy pool
│   └── ai/
│       └── client.py          # OpenAI + Anthropic wrapper, token accounting
├── tasks/
│   ├── celery_app.py
│   ├── profile_tasks.py       # NEW
│   ├── discovery_tasks.py
│   ├── audit_tasks.py
│   ├── send_tasks.py
│   └── sequence_tasks.py      # NEW — celery beat scans due follow-ups + reply polling
├── alembic/                   # NEW — migrations dir (env.py + versions/)
├── alembic.ini
├── tests/                     # NEW
│   ├── test_discovery.py
│   ├── test_audit.py
│   ├── test_email_finder.py
│   └── test_sender_limits.py
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 3. MODULE SPECIFICATIONS

### 3.0 SELLER PROFILING — Agency Brain (NEW)

**File**: `backend/services/profile/agency_analyzer.py`

```python
"""
Agency Profiling.

Input: the user's OWN agency website (e.g. trax9.com).
Output: structured understanding of what the agency sells and who it should target.
This drives (a) auto-generated discovery keywords and (b) email personalization
(matching an agency service to a lead's specific gap).

Run ONCE per agency, re-runnable. Cached in agency_profiles.
"""

import httpx
from bs4 import BeautifulSoup
from typing import Dict, List
from ..ai.client import ai_completion, ai_json

async def analyze_agency(url: str, user_id: str) -> Dict:
    """
    Steps:
    1. Normalize URL. Fetch homepage + up to 6 internal pages
       (services, about, portfolio, pricing, industries, contact)
       via the shared scraping browser (JS-rendered sites common for agencies).
    2. Strip to text, trim each page ~2500 chars.
    3. Feed combined text to AI with the Agency Analysis prompt (see §11).
    4. AI returns JSON:
       {
         "company_name": str,
         "services": [{"name": str, "description": str}],   # e.g. Web Dev, SEO, Meta Ads
         "ideal_client": {
             "industries": [str],       # e.g. ["ecommerce - fashion", "restaurants"]
             "company_size": str,        # "small-medium local businesses"
             "geos": [str],              # markets they serve
             "buying_signals": [str]     # "runs ads but poor SEO", "outdated Shopify theme"
         },
         "positioning": str,
         "suggested_keywords": [str],    # 10-20 discovery seed keywords
         "suggested_locations": [str]    # target cities
       }
    5. Upsert agency_profiles row. Return it.

    Notes:
    - suggested_keywords + suggested_locations are pre-filled into the
      Create-Campaign form so the user starts from the agency's real ICP,
      not a generic list.
    """
    pass
```

Endpoint: `POST /api/profile/analyze {website}` → runs `profile_tasks.analyze_agency_task` → returns profile. `GET /api/profile` returns saved profile.

---

### 3A. LEAD DISCOVERY ENGINE

Three sources run in parallel; results merged + de-duplicated on normalized domain (strip scheme/www/trailing slash), scoped to `user_id`. New leads insert with `status='discovered'`.

**Cross-cutting rules for all three:**
- Keywords/locations default from `agency_profiles.suggested_keywords/locations` when the campaign is linked to a profile.
- Playwright sources MUST use `services/scraping/browser.py` (proxy + UA rotation) — never a raw `async_playwright()`.
- Each task increments `tasks.completed_items` in its chord callback; Places calls increment `usage_counters.places_calls`.

**File**: `backend/services/discovery/google_maps.py`

```python
"""
Google Maps/Places API Lead Discovery.

API: Google Places API (New) — Text Search + Place Details (httpx, NOT a browser).
Free tier: $200/month credit (~40K calls).
"""
import httpx
from typing import List, Dict

async def search_places(
    keyword: str,
    location: str,
    radius: int = 50000,      # meters
    max_results: int = 60,
    api_key: str = None,      # per-user key from user_settings
) -> List[Dict]:
    """
    Flow:
    1. Geocode the location string to lat/lng (or pass as text).
    2. Places API textSearch with keyword + location.
    3. Paginate via nextPageToken (2s delay between pages) to exceed the
       20-result default, up to max_results.
    4. For each place_id, call Place Details for website, phone, formatted_address.
    5. Filter: only return entries WITH a website.
    6. Return list of dicts:
       company_name, website (normalized, www stripped), phone, address,
       city, country, category (types[0]), source="google_maps"
    """
    pass
```

**File**: `backend/services/discovery/google_search.py`

```python
"""
Google Custom Search Lead Discovery.

API: Google Custom Search API. Free: 100 queries/day. Paid: $5 per 1K.
"""
import httpx
from typing import List, Dict
from urllib.parse import urlparse

# Never return these as leads
EXCLUDE_DOMAINS = [
    'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'linkedin.com',
    'yelp.com', 'yellowpages.com', 'tripadvisor.com', 'pinterest.com',
    'youtube.com', 'wikipedia.org', 'amazon.com', 'reddit.com',
    # platform/CMS homepages, not businesses
    'wix.com', 'squarespace.com', 'shopify.com', 'wordpress.com', 'godaddy.com',
]

async def search_google(
    keyword: str,
    location: str,
    pages: int = 3,
    api_key: str = None,
    cx: str = None,
) -> List[Dict]:
    """
    Query templates:
      "{keyword} {location}"
      "{keyword} website {location}"
      "best {keyword} in {location}"
      "{keyword} online store {location}"

    For each result:
    1. Extract domain from URL.
    2. Skip EXCLUDE_DOMAINS.
    3. Clean page title -> company_name hint (strip " | Home", " - Official Site").
    4. Return: company_name, website (domain), source="google_search", search_query
    """
    pass
```

**File**: `backend/services/discovery/directory_scraper.py`

```python
"""
Directory-based Lead Discovery. Playwright via services/scraping/browser.py
(proxy + UA rotation REQUIRED — these sites ban fast).
"""
from typing import List, Dict
from ..scraping.browser import get_page   # proxied, UA-rotated

DIRECTORIES = {
    "yelp": {
        "url_template": "https://www.yelp.com/search?find_desc={keyword}&find_loc={location}",
        "card_selector": "div[data-testid='search-results'] > div",
        "name_selector": "a[data-testid='business-name']",
        "website_selector": "a[data-testid='business-website']",
    },
    "yellowpages": {
        "url_template": "https://www.yellowpages.com/search?search_terms={keyword}&geo_location_terms={location}",
        "card_selector": "div.result",
        "name_selector": "a.business-name",
        "website_selector": "a[class*='website']",
    },
}

async def scrape_directory(
    directory: str,
    keyword: str,
    location: str,
    max_pages: int = 3,
) -> List[Dict]:
    """
    Flow:
    1. Acquire a proxied page from scraping.browser.
    2. Navigate to the search URL; wait for card_selector (10s timeout).
    3. Extract business name, website URL, phone per card.
    4. Politeness delay + jitter, then click "Next" if present.
    5. Release the page.
    6. Return: company_name, website, phone, source=<directory name>

    On 403/429: back off exponentially; after N consecutive blocks the
    circuit-breaker in browser.py pauses this source.
    """
    pass
```

---

### 3A.5 EMAIL FINDER (NEW)

**File**: `backend/services/discovery/email_finder.py`

```python
"""
Find a contact email for a lead. Runs after discovery, before/with audit.
Without this the lead has no send target.

Order of attempts (stop at first confident hit):
1. SCRAPE: fetch homepage + /contact + /about, regex emails, mailto: links.
   Prefer role addresses on the lead's own domain (info@, hello@, contact@,
   sales@). Reject free-mail (gmail/yahoo) unless nothing else.
   confidence ~90 if found on-site.
2. PATTERN GUESS: if a person name is found (About/team), build common
   patterns (first@, first.last@, firstlast@ domain). confidence ~40.
   Do NOT SMTP-verify (risky); mark low confidence.
3. HUNTER.IO (optional, only if user provided a Hunter key): domain search.
   confidence from Hunter's own score.

Returns: {"email": str|None, "source": "scraped|pattern|hunter",
          "confidence": int}

Sets lead.email, lead.email_source, lead.email_confidence.
If None → lead.status stays but is skipped at send time (flag in UI).
"""
async def find_email(company_name: str, website: str, hunter_key: str = None) -> dict:
    pass
```

Leads with no email are surfaced in the dashboard as "needs email" so the user can add one manually.

---

### 3B. AUDIT ENGINE

**Cross-cutting rules:**
- **Cache**: before auditing a domain, check `audit_cache` (fetched < 7 days). Reuse if fresh; else audit and upsert.
- All browser-based audits use `services/scraping/browser.py` (proxy + UA rotation).
- Partial audit is saved even if one sub-audit fails; failures recorded under `audit_data["errors"]`.
- Import `datetime` explicitly wherever used.

**File**: `backend/services/audit/website.py`

```python
"""
Website Audit. httpx + BeautifulSoup. No external API needed.
"""
import httpx
from bs4 import BeautifulSoup
from typing import Dict

TECH_STACK_PATTERNS = {
    'shopify':     ['cdn.shopify.com', 'myshopify.com', '/cdn/shop/', 'shopifycdn'],
    'wordpress':   ['/wp-content/', '/wp-admin', '/wp-includes', 'wp-json'],
    'woocommerce': ['/wp-content/plugins/woocommerce', 'woocommerce'],
    'magento':     ['/skin/frontend/', 'Magento', 'mage/'],
    'wix':         ['wix.com', 'WixEditor'],
    'webflow':     ['webflow', 'Webflow'],
    'squarespace': ['squarespace.com', 'Squarespace'],
    'react':       ['react', 'next.js', 'Next.js'],
    'vue':         ['vuejs', 'vue.js'],
    'angular':     ['angular', 'Angular'],
    'jquery':      ['jquery', 'jQuery'],
}

async def audit_website(url: str) -> Dict:
    """
    1. Normalize URL (add https:// if missing). Fetch homepage (timeout 15s).
    2. Parse with BeautifulSoup.
    3. Tech stack: scan HTML body, meta generator tag, and script src URLs
       against TECH_STACK_PATTERNS -> detected platforms list.
    4. Page analysis:
       - title: exists, length
       - meta description: exists, length
       - Open Graph: og:title, og:description, og:image, og:url
       - Twitter card tags
       - h1 exists + count; all h1-h6 counts
       - images: total, with alt, without alt, alt ratio
       - links: total, internal, external
       - schema markup: JSON-LD + Microdata types
       - favicon, viewport meta, canonical, html lang, charset
    5. Performance: load time (httpx timing), page size KB,
       linked-resource count.
    6. Security: SSL valid, HSTS header, X-Frame-Options.
    7. Contact extraction: email + phone regex; contact page URL from nav.
    8. Social: Facebook / Instagram / LinkedIn / Twitter URLs found on page.
       (These are REUSED later by audit/social.py — do not re-discover.)

    Returns dict of all findings.
    """
    pass
```

**File**: `backend/services/audit/seo.py`

```python
"""
SEO Audit. Google PageSpeed API (free, unlimited w/ key) + on-page analysis.
"""
import httpx
from typing import Dict

PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

async def audit_seo(url: str, onpage_data: Dict = None, api_key: str = None) -> Dict:
    """
    Part 1 — PageSpeed Insights (strategy=MOBILE and DESKTOP):
      performance_score, accessibility_score, best_practices_score, seo_score
      (all 0-100), fcp, lcp, cls, tbt, speed_index,
      opportunities[], diagnostics[]
      Cached via audit_cache; re-fetch only if > 24h old.

    Part 2 — On-page SEO score from onpage_data (max 100):
      +20 title exists, 30-60 chars, has keyword
      +15 meta description exists, 120-160 chars
      +10 h1 exists
      +10 Open Graph tags present
      +10 schema markup present
      +10 alt text ratio > 80%
      +10 canonical tag present
      + 5 viewport meta
      + 5 favicon
      + 5 SSL valid

    Part 3 — Technical SEO:
      robots.txt exists + allows all
      sitemap.xml exists + URL count
      Flag issues: missing sitemap, robots blocking, no canonical,
      duplicate meta tags, missing alt text, slow page speed.
    """
    pass
```

**File**: `backend/services/audit/meta_ads.py`

```python
"""
Meta Ads Library scraper. Public page, React-rendered -> Playwright via
services/scraping/browser.py (proxy + UA rotation REQUIRED).

URL: https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q={term}
Rate: ~10 searches/min per IP. Throttle hard.
"""
from typing import Dict
from ..scraping.browser import get_page

async def audit_meta_ads(company_name: str, website: str = None) -> Dict:
    """
    Strategy:
    1. Search by exact company name.
    2. If 0 results, try: domain without TLD, brand short name.
    3. For a search with results:
       - count total active ads
       - extract up to 5 sample creatives: primary text, headline,
         description, CTA type, media type (image/video/carousel),
         landing page URL, ad start date
       - handle the "See More" text expander
       - note patterns (discount-focused, brand-awareness, etc.)

    Implementation: viewport 1920x1080, recent-Chrome UA (from the pool),
    wait for ad cards up to 10s, release page after each search.

    Returns:
    {
      "has_ads": bool,
      "total_active_ads": int,
      "sample_ads": [{"headline","primary_text","cta","media_type",
                      "landing_page","start_date"}],
      "ad_strategies": [str],
      "ad_library_url": str
    }
    """
    pass
```

**File**: `backend/services/audit/brand_rnd.py`

```python
"""
Brand R&D. Fetches internal pages, feeds to AI, returns structured analysis.
"""
from typing import Dict
from ..ai.client import ai_json

async def analyze_brand(url: str, audit_data: Dict) -> Dict:
    """
    1. Fetch: homepage, about, services/products, plus any relevant nav page (3-5 total).
    2. Extract text (strip HTML), trim each to ~2000 chars.
    3. Send with the Brand Analysis prompt (see §11).
    4. Parse JSON response:
       industry, target_audience, brand_positioning,
       website_quality_score (1-10), strengths[3], weaknesses[3],
       estimated_size (small/medium/large enterprise),
       pain_points[], best_services[], competitor_notes

    Returns parsed dict. Record tokens in usage_counters.ai_tokens.
    """
    pass
```

**NEW File**: `backend/services/audit/google_ads.py`

```python
"""
Google Ads Transparency Center scraper.

URL: https://adstransparency.google.com/?region=anywhere&query={advertiser}
Public, JS-rendered → Playwright via shared browser (proxy + UA rotation).

Extract:
- is_advertiser: bool (do they run Google ads at all?)
- total_ads: int
- formats: [str]  (text/image/video)
- sample_ads: up to 5 {format, preview_text, last_shown, landing_url}
- regions: [str]

Strategy: search by company_name; if empty, try domain.
Rate: ~10/min per IP → rotate proxies, 6s min delay between searches.

Returns dict stored under audit_data["google_ads"].
"""
async def audit_google_ads(company_name: str, website: str = None) -> dict:
    pass
```

---

### 3B.5 LEAD SCORING (NEW)

**File**: `backend/services/audit/scoring.py`

```python
"""
Compute a 0-100 fit score after audit so the user emails the hottest leads first.

Higher score = better prospect for THIS agency (uses agency_profiles.ideal_client).

Heuristic (tune weights):
+  Website weak (low PageSpeed, no schema, poor SEO)      → they NEED the service
+  Already running Meta/Google ads                        → they HAVE budget + intent
+  Industry matches agency ideal_client.industries        → fit
+  Has a real business email found                        → reachable
+  Tech stack matches agency capability (e.g. Shopify)    → agency can help fast
-  Enterprise / already high-quality site                 → unlikely to convert
-  No email found                                         → hard to reach

Returns {"fit_score": int, "score_reasons": [{"factor": str, "points": int}]}.
Set lead.fit_score, lead.score_reasons, status='scored'.
"""
def score_lead(lead: dict, audit_data: dict, ideal_client: dict) -> dict:
    pass
```

---

### 3B.6 SOCIAL ENRICHMENT — SocialCrawl (NEW)

**File**: `backend/services/audit/social.py`

```python
"""
Social + review enrichment via SocialCrawl API (https://socialcrawl.dev).

WHY: adds organic-social + review-site signals the website/ad audits can't see —
richer email hooks and a sharper re-score. NOT core discovery/audit.

COST-GATED: only runs on leads whose fit_score >= SOCIAL_ENRICH_MIN_SCORE
(default 60). SocialCrawl bills per credit; enriching every lead burns credits
fast (200 leads x 3 lookups = 600 credits/wk). Enrich only the hottest.

AUTH: per-user key from user_settings.encrypted_keys["socialcrawl"]. If the user
has no key, this module is a no-op (skip silently, status -> enriched with
social_data = {"skipped": "no_key"}). Multi-tenant: never share a global key.

Unified API — one token, one schema (author, engagement, metadata) across 44
platforms. HTTP + Bearer key header. Free tier 100 credits to start.

WHAT TO PULL (keep it lean — 2-4 calls per lead, not the whole catalog):
1. Review sites: Trustpilot / TripAdvisor / Google rating + review_count for the
   lead's brand/domain  -> strong email angle + score signal.
2. LinkedIn company: employee_count, industry, followers -> company size / fit.
3. One primary social handle (IG or TikTok, inferred from the site's social
   links found in website audit): followers + recent engagement -> "big audience,
   weak site" hook.

Endpoints: use SocialCrawl "Universal Search" to resolve the brand across
platforms in one request where possible, then targeted profile/review endpoints
for the matched entities. Respect the typed responses + built-in retries.

IMPLEMENTATION:
- httpx async client, timeout 20s, Bearer <socialcrawl_key>.
- Reuse social URLs already extracted by audit/website.py (don't re-discover).
- Handle 402/quota-exhausted gracefully -> social_data = {"error": "quota"},
  do NOT fail the lead.
- Increment usage_counters (add a socialcrawl_credits column, or reuse a generic
  external_calls counter) so cost is visible per user.

RETURNS (stored under audit_data["social"]):
{
  "reviews": {"platform": str, "rating": float, "count": int, "url": str} | None,
  "linkedin": {"employees": int|None, "industry": str|None, "followers": int|None} | None,
  "primary_social": {"platform": str, "handle": str, "followers": int,
                     "engagement_rate": float} | None,
  "signals": [str],          # AI/heuristic: "large audience, weak site",
                             # "poor Trustpilot rating", "no social presence"
  "credits_used": int
}

AFTER ENRICH: re-run scoring.score_lead with social signals folded in
(e.g. + points for big audience + running ads but weak site = prime CRO lead;
- points for excellent reviews + strong site). Update lead.fit_score,
lead.score_reasons. Set status='enriched'.
"""
async def enrich_social(lead: dict, audit_data: dict, socialcrawl_key: str | None) -> dict:
    pass
```

**Scoring feedback loop**: after `enrich_social`, call `scoring.score_lead` again with `audit_data["social"]` available so the fit score reflects social/review reality. The email writer then receives the enriched score + signals.

**Config**: `SOCIAL_ENRICH_MIN_SCORE` (default 60) in `config.py`; per-user SocialCrawl key in `user_settings`.

---

### 3C. AI EMAIL WRITER

**File**: `backend/services/email/writer.py`

```python
"""
AI Email Generation. Personalized cold outreach from audit data.
Provider = user_settings.ai_provider (openai | anthropic).
Claude Haiku 4.5 recommended: cheap + high quality for this task.
"""
from typing import Dict
from ..ai.client import ai_completion

def pick_top_gap(audit: Dict, agency_services: list) -> str:
    """
    Choose the single biggest, most addressable gap the agency can fix.
    Priority order (first match wins):
    1. Running paid ads (Meta or Google) + weak site/speed  -> highest intent:
       they're paying for traffic that lands badly. Pitch CRO / web dev.
    2. Poor review rating (from audit_data["social"])        -> reputation work.
    3. Large social audience + weak site                     -> conversion gap.
    4. No schema / poor SEO score + no ads                   -> organic SEO.
    5. Outdated or DIY platform                              -> rebuild.
    Return a short human phrase naming the gap.
    """
    pass

def build_email_context(lead: Dict, audit: Dict) -> str:
    """
    Structured context string for the prompt:

    Company / Website / Industry / Tech Stack

    Website Audit:
      Performance score, has schema, mobile friendly, has blog,
      has contact page, social media present

    SEO Issues: [...]

    Meta Ads:   running ads, active count, sample strategy
    Google Ads: is advertiser, formats
    Social:     review rating + count, LinkedIn size, follower count,
                derived signals   (only present if enriched)

    Brand Analysis:
      target audience, positioning, pain points, recommended services
    """
    pass

async def generate_email(
    lead: Dict, audit: Dict, agency_services: list, provider: str, key: str
) -> Dict:
    """
    1. context   = build_email_context(lead, audit)
    2. top_gap   = pick_top_gap(audit, agency_services)
    3. Send the Email Writing system prompt (§11) with context + top_gap
       + agency_services. temperature=0.7
    4. Parse response: first line -> subject (strip "Subject:"), rest -> body.
    5. Record tokens in usage_counters.ai_tokens.

    Returns {"subject": str, "body": str}
    """
    pass
```

**What changed from a naive writer** (why this beats a template):
- Injects `agency_profiles.services` — the AI can only pitch what the agency actually sells.
- Injects `audit_data["social"].signals` when present (e.g. "42k TikTok followers but no schema + slow site", "3.1★ Trustpilot") — the sharpest, most specific hooks available.
- `pick_top_gap` forces ONE service matched to ONE real finding, so a shop running ads onto a slow page hears about the page, not generic SEO.

Full email system prompt in §11.

---

### 3D. EMAIL SENDER (+ Warmup, Unsubscribe, Redis limits)

**File**: `backend/services/email/sender.py`

```python
"""
Email Sending via SendGrid. Free tier: 100/day forever.
Keys + sending config come from user_settings (per-user, decrypted at call time).
"""
import sendgrid
from sendgrid.helpers.mail import Mail, TrackingSettings, OpenTracking, Header
from datetime import datetime
from typing import Dict, List

def check_rate_limit(user_id: str, settings: Dict) -> tuple[bool, str]:
    """
    ALL counters live in Redis (see mandatory change 1 below), never in
    process globals.

    Checks, in order — first failure short-circuits:
    1. Send window: now must be between send_start_hour and send_end_hour.
    2. Hourly:  INCR sent:{user_id}:{YYYY-MM-DD-HH}  < max_emails_per_hour
    3. Daily:   INCR sent:{user_id}:{YYYY-MM-DD}     < effective_daily_cap()
    4. Monthly quota (see change 5).

    Returns (allowed: bool, reason: str).
    """
    pass

def effective_daily_cap(settings: Dict) -> int:
    """min(max_emails_per_day, warmup_daily_cap) when warmup_enabled, else max."""
    pass

def build_footer(settings: Dict, token: str) -> str:
    """
    CAN-SPAM footer, appended to EVERY email incl. follow-ups:
      - unsubscribe link -> {APP_BASE_URL}/api/u/{token}
      - the account's physical postal address (required field in settings)
    """
    pass

def send_email(lead: Dict, user_id: str, settings: Dict) -> Dict:
    """
    Flow:
    1. Suppression check (change 3). If suppressed -> status 'unsubscribed', stop.
    2. check_rate_limit. If blocked -> status 'queued', stop (retried later).
    3. Build Mail:
       From: FROM_NAME <FROM_EMAIL>
       To: lead.email
       Subject: lead.email_subject
       HTML body: lead.email_body (newlines -> <br>) + build_footer(...)
       Plain text alternative
       Header: List-Unsubscribe: <{APP_BASE_URL}/api/u/{token}>
       Header: List-Unsubscribe-Post: List-Unsubscribe=One-Click
    4. Enable open tracking.
    5. Send via SendGrid.
    6. On success: lead.status='sent', lead.sent_at=now,
       EmailLog(status='sent', message_id=response X-Message-Id),
       increment usage_counters.emails_sent, increment Redis counters.
    7. On failure: 4xx -> status 'bounced' + reason.
                   5xx/timeout -> retry up to 3x, exponential backoff.

    Returns {"status": str, "message_id": str|None}
    """
    pass
```

**Mandatory changes over a naive sender:**

1. **Rate-limit state in Redis, not process globals.** Keys: `sent:{user_id}:{YYYY-MM-DD-HH}` and `:{YYYY-MM-DD}` with TTL. Correct across all workers + restarts. (A process-global counter is wrong the moment you run more than one worker, and resets on every restart.)
2. **Warmup state machine.** If `warmup_enabled`: effective daily cap = `min(max_emails_per_day, warmup_daily_cap)`. A daily celery-beat job raises `warmup_daily_cap` by +5/day from a start of 10 until it reaches `max_emails_per_day`.
3. **Suppression check.** Before send, if `to_email` in `suppressions` for this user → skip, mark `status='unsubscribed'`.
4. **Unsubscribe + CAN-SPAM footer** injected into every email: one-click unsubscribe link (`/api/u/{signed_token}`) + agency physical address. Also set `List-Unsubscribe` header.
5. **Quota check.** If `usage_counters.emails_sent` for the month ≥ `users.monthly_email_quota` → block + surface in UI.
6. Increment `usage_counters.emails_sent` on success.

Unsubscribe endpoint: `GET /api/u/{token}` → verify signed token → insert suppression → friendly "you're unsubscribed" page. Public, no auth.

---

### 3D.5 REPLY TRACKER (NEW)

**File**: `backend/services/email/reply_tracker.py`

```python
"""
Detect replies so status 'replied' actually gets set (and follow-ups auto-cancel).

Two supported modes (user picks in settings):
A. IMAP POLL (works with Gmail/Workspace/any inbox):
   - Celery-beat task every 5 min.
   - IMAP search UNSEEN since last check.
   - For each message, match sender email OR In-Reply-To/References header
     against email_logs.message_id → resolve the lead.
   - On match: lead.status='replied', lead.replied_at=now,
     email_log.status='replied', cancel scheduled sequence_steps for that lead.
B. SENDGRID INBOUND PARSE:
   - MX on a subdomain → POSTs inbound mail to /api/webhook/inbound.
   - Same matching + update logic.

Never auto-reply. Just record + stop the sequence.
"""
async def poll_replies(user_id: str): ...
async def handle_inbound(payload: dict): ...
```

---

### 3E. FOLLOW-UP SEQUENCER (NEW)

**File**: `backend/services/email/sequencer.py` + `tasks/sequence_tasks.py`

```python
"""
Multi-step follow-ups. When first email is sent, schedule steps:
  step 1  → +3 days  ("just floating this back up" + a second specific insight)
  step 2  → +7 days  (short break-up email)
Configurable per campaign.

- On first send, create sequence_steps rows (status='scheduled', scheduled_for=...).
- Celery-beat task (every 15 min) selects due steps where the lead has NOT
  replied / unsubscribed / bounced, generates the step body via writer.py
  (context: prior email + audit), sends via sender.py, marks step 'sent'.
- Any reply / unsubscribe / bounce cancels all remaining steps for that lead.
- Warmup + rate limits + suppression all still apply to follow-ups.
"""
```

---

### 3F. WEB DASHBOARD (API Endpoints)

```
AUTH:
POST   /api/register              # email, password, name
POST   /api/login                 # returns JWT
GET    /api/me                    # current user

CAMPAIGNS:
GET    /api/campaigns             # list (paginated)
POST   /api/campaigns             # create (name, keywords, locations)
GET    /api/campaigns/{id}
PUT    /api/campaigns/{id}
DELETE /api/campaigns/{id}        # + its leads
POST   /api/campaigns/{id}/discover   # trigger discovery
POST   /api/campaigns/{id}/audit      # audit discovered leads
POST   /api/campaigns/{id}/write      # generate emails
POST   /api/campaigns/{id}/send       # send written emails
GET    /api/campaigns/{id}/tasks      # task progress
GET    /api/campaigns/{id}/leads      # paginated, filterable

LEADS:
GET    /api/leads/{id}            # detail incl. audit_data
POST   /api/leads/{id}/send       # send single
POST   /api/leads/{id}/regenerate # regenerate email
PUT    /api/leads/{id}            # edit email / notes / manual email address
DELETE /api/leads/{id}

ANALYTICS:
GET    /api/dashboard/stats       # total leads, audited, sent, replied
GET    /api/dashboard/chart       # daily email activity, last 30 days
```

Plus, new in v2:

```
PROFILE:
POST   /api/profile/analyze        # analyze agency website
GET    /api/profile                # get saved agency profile

LEADS:
GET    /api/leads?status=&min_score=&needs_email=   # filter + sort by fit_score
GET    /api/campaigns/{id}/export  # CSV export of leads

SETTINGS:
GET    /api/settings               # returns masked keys + sending config
PUT    /api/settings               # save (encrypts keys via crypto.py)

SEQUENCES:
GET    /api/leads/{id}/sequence    # scheduled follow-ups
POST   /api/leads/{id}/sequence/cancel

PUBLIC:
GET    /api/u/{token}              # one-click unsubscribe (no auth)

WEBHOOKS:
POST   /api/webhook/email          # SendGrid events (opens/bounces/clicks/spam)
POST   /api/webhook/inbound        # SendGrid inbound parse (replies)

HEALTH:
GET    /health                     # DB + Redis ping, returns 200/503
```

Every authed endpoint filters by `current_user.id`. No cross-tenant reads.

---

## 4. FRONTEND SPEC (React + Vite + Tailwind)

```
frontend/
├── index.html
├── vite.config.js
├── tailwind.config.js
├── postcss.config.js
├── package.json
└── src/
    ├── main.jsx
    ├── App.jsx
    ├── index.css                   # Tailwind imports + Trax9 brand colors
    ├── api/client.js               # Axios instance + auth interceptor
    ├── context/AuthContext.jsx     # auth state, login/logout
    ├── pages/
    │   ├── Login.jsx               # login + register
    │   ├── Onboarding.jsx          # NEW — agency analyze first-run
    │   ├── Dashboard.jsx           # stats + chart + recent leads
    │   ├── Campaigns.jsx           # list + create modal
    │   ├── CampaignDetail.jsx      # view + leads table + actions
    │   ├── LeadDetail.jsx          # audit tabs + email
    │   └── Settings.jsx
    └── components/
        ├── Layout.jsx  Sidebar.jsx  Header.jsx
        ├── StatsCard.jsx  ActivityChart.jsx      # Recharts line chart
        ├── LeadsTable.jsx  LeadFilters.jsx
        ├── AuditPanel.jsx
        │   ├── WebsiteTab.jsx  SeoTab.jsx  MetaAdsTab.jsx
        │   ├── GoogleAdsTab.jsx                  # NEW
        │   ├── BrandTab.jsx  SocialTab.jsx       # NEW
        ├── EmailPreview.jsx        # subject + body editor w/ preview
        ├── SequenceTimeline.jsx    # NEW
        ├── ProfileCard.jsx         # NEW
        ├── ProgressBar.jsx  CampaignForm.jsx
        └── StatusBadge.jsx  ScoreBadge.jsx  EmptyState.jsx
```

**Page designs**
- **Login** — centered card, Trax9 logo, email + password, register link. Minimal.
- **Dashboard** — 4 stat cards (Total Leads, Audited, Sent, Replied); Recharts line chart of emails/day last 30 days; recent 10 leads table.
- **Campaigns** — "New Campaign" top-right; table (Name, Keywords, Locations, Lead count, Status, Created, Actions); status badge Draft=gray / Running=blue / Completed=green / Paused=yellow; row click → detail.
- **Create Campaign modal** — name; seed keywords (tag input, type+Enter, X to remove); target locations (tag input); industry filter (multi-select). **Keywords + locations pre-filled from the agency profile**, not a generic list.
- **Campaign Detail** — header (name, date, status); action row: Discover Leads / Audit All / Write Emails / Send All; progress bar when a task runs; filters (status, search, date); leads table (Company, Website, Email, City, Status badge, Fit Score, audited check, email preview, Send/Delete); pagination.
- **Lead Detail** — back; company header (name, clickable website, email, phone, address); audit tabs; email section (editable subject + body, preview toggle, Regenerate, Send, sent timestamp).
- **Settings** — sections for API keys, sending config, account. Save per section.

**v2 additions:**

- **`pages/Onboarding.jsx`** — first-run: paste agency website → shows extracted services + ideal client + suggested keywords → "Create your first campaign" pre-filled.
- **`components/ProfileCard.jsx`** — shows the agency brain output; re-analyze button.
- **`LeadsTable.jsx`** — add **Fit Score** column (sortable, colored), **Email** column with "needs email" badge + inline add, and a **min-score** filter.
- **`AuditPanel`** — add **GoogleAdsTab.jsx** next to MetaAdsTab.
- **`components/SequenceTimeline.jsx`** — shows step 1 / 2 schedule + status on Lead Detail.
- **`Settings.jsx`** — add: AI provider toggle (OpenAI/Anthropic), Hunter.io key, **SocialCrawl key + enrich-min-score slider**, IMAP config (host/user/pass) OR inbound-parse toggle, warmup on/off, physical address (required for footer), monthly quota display.
- **`components/SocialTab.jsx`** — in AuditPanel: review rating, LinkedIn size, primary-social followers/engagement, derived signals. Empty state when lead wasn't enriched (below score threshold or no key).
- Reusable **`ScoreBadge.jsx`**, **`EmptyState.jsx`**, global error toast + auth-expiry redirect.

**Tech**: React 18 + Vite 5 + Tailwind 3 + React Router 6 + Recharts + lucide-react + date-fns + axios.

**File**: `frontend/tailwind.config.js`

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        trax9: {
          navy:  '#1a2744',
          gold:  '#f5a623',
          bg:    '#f7f8fb',
          text:  '#1f2430',
          muted: '#6b7280',
          line:  '#e6e8ee',
        }
      }
    }
  },
  plugins: []
}
```

---

## 5. TASK QUEUE (Celery + Redis)

**File**: `backend/tasks/celery_app.py`

```python
from celery import Celery
from kombu import Queue, Exchange
import os

celery_app = Celery(
    'trax9_tasks',
    broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    backend=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
)

celery_app.conf.task_queues = [
    Queue(q, Exchange(q), routing_key=f'{q}.#')
    for q in ('profile', 'discovery', 'email_find', 'audit', 'email')
]

celery_app.conf.task_routes = {
    'tasks.profile_tasks.*':    {'queue': 'profile'},
    'tasks.discovery_tasks.*':  {'queue': 'discovery'},
    'tasks.audit_tasks.*':      {'queue': 'audit'},
    'tasks.send_tasks.*':       {'queue': 'email'},
    'tasks.sequence_tasks.*':   {'queue': 'email'},
}

celery_app.conf.worker_concurrency = 5
celery_app.conf.task_acks_late = True             # re-queue on worker loss
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.task_soft_time_limit = 300        # 5 min
celery_app.conf.task_time_limit = 600             # 10 min hard
```

**Execution flows:**
- **Discover** → master task builds a chord: header = one task per keyword×location×source, callback = `save_discovered_leads` (dedup + insert + mark task complete).
- **Audit All** → chord of `audit_single_lead` per lead. Each lead runs website → SEO → Meta ads → Google ads → Brand R&D sequentially, then scores, then enriches if ≥ threshold, then re-scores. Concurrency governed by `worker_concurrency`.
- **Send All** → batched (see the fix below).

**Config notes:**

- Queues: `profile`, `discovery`, `email_find`, `audit`, `email` (5) + a separate `beat` scheduler process.
- **Celery beat schedule**:
  - `raise_warmup_caps` — daily 00:05
  - `run_due_sequences` — every 15 min
  - `poll_all_replies` — every 5 min (per user with IMAP configured)
- Every task that fans out with a chord has a callback that updates `tasks.completed_items/failed_items` and flips `tasks.status` to `completed`/`failed`.
- **`send_tasks.py` batching — get this right.** Split written leads into batches of 10 and enqueue every batch up front with `countdown = batch_index * 60` so they space out across time. Do NOT have the task re-queue itself and `break` out of the batch loop — that pattern silently sends only the first batch and drops the rest.
- Scraping tasks catch + record failures per item; a dead item → `status='failed'`, not a stuck chord.

---

## 6. CONFIGURATION

`backend/config.py` — global infra only (DB, Redis, JWT secret, Fernet key, default rate limits, proxy pool URL). **Per-user API keys (OpenAI/SendGrid/Google/Hunter) live encrypted in `user_settings`**, decrypted on demand via `deps.get_user_settings`. This is what makes it real multi-tenant SaaS instead of one shared key.

```python
# config.py essentials (env)
SECRET_KEY, DATABASE_URL, REDIS_URL
FERNET_KEY                # 32-byte urlsafe base64 — encrypts user_settings.encrypted_keys
PROXY_POOL               # comma-sep proxy URLs, or a rotating-proxy endpoint
DEFAULT_MAX_EMAILS_PER_DAY=100, DEFAULT_MAX_EMAILS_PER_HOUR=50
SEND_START_HOUR=8, SEND_END_HOUR=18
SOCIAL_ENRICH_MIN_SCORE=60   # only enrich (SocialCrawl) leads at/above this fit score
APP_BASE_URL             # for unsubscribe links
SENTRY_DSN (optional)
```

`.env.example` updated accordingly. **Never** commit real keys; `FERNET_KEY` generated once per deploy.

---

## 7. SECURITY, COMPLIANCE & ANTI-BAN (NEW)

**Secrets**
- `crypto.py`: Fernet encrypt/decrypt. `user_settings.encrypted_keys` stores a JSON blob of all third-party keys, encrypted with `FERNET_KEY`. Settings API returns only masked values (`sk-...abcd`).
- JWT signed with `SECRET_KEY`, 72h expiry, bcrypt password hashing.

**Email compliance (CAN-SPAM / GDPR basics)**
- Every email: unsubscribe link + one-click `List-Unsubscribe` header + physical postal address in footer.
- `suppressions` table honored before every send (incl. follow-ups).
- Hard bounces + spam reports auto-add to suppressions.
- Store consent basis note; provide per-lead "do not contact".

**Anti-ban scraping** (`services/scraping/browser.py`, `proxies.py`)
- Rotating proxy pool + realistic rotating user-agents + randomized viewport.
- Per-domain politeness delay + jitter; concurrency cap per target host.
- Exponential backoff on 429/403; circuit-breaker that pauses a source after N consecutive blocks.
- Respect robots where legally required; Meta/Google ad libraries are public but rate-limited — throttle hard.

**Webhook security**
- SendGrid event webhook: verify Ed25519 signature header. Deduplicate by `sg_event_id` (store in `email_logs.sg_event_ids`).

**Cost guardrails**
- `usage_counters` enforce monthly email quota + track AI tokens + Places calls.
- Per-user kill switch (`campaigns.status='paused'`) auto-trips if quota exceeded.

---

## 8. OBSERVABILITY, HEALTH & TESTS (NEW)

- **Structured logging** (JSON) across services; request-id middleware.
- **Sentry** (optional via `SENTRY_DSN`) for backend + Celery.
- **`GET /health`** pings DB + Redis; used by Docker healthcheck + deploy platform.
- **FastAPI global exception handler** → clean JSON errors, never leak stack traces.
- **Tests** (`pytest`):
  - `test_discovery.py` — dedup + domain normalization
  - `test_email_finder.py` — regex extraction + free-mail rejection
  - `test_audit.py` — tech-stack detection on fixture HTML
  - `test_sender_limits.py` — Redis rate-limit + suppression + warmup cap
  - Mock all external HTTP (respx/responses); no live calls in CI.

---

## 9. DEPLOYMENT

**File**: `docker-compose.yml`

```yaml
version: '3.8'

services:
  db:
    image: postgres:15-alpine
    volumes: [pgdata:/var/lib/postgresql/data]
    environment:
      POSTGRES_DB: trax9_leads
      POSTGRES_USER: trax9
      POSTGRES_PASSWORD: ${DB_PASSWORD:-password}
    ports: ["5432:5432"]
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    restart: unless-stopped

  backend:
    build: { context: ./backend, dockerfile: Dockerfile }
    ports: ["8000:8000"]
    depends_on: [db, redis]
    env_file: [.env]
    environment:
      DATABASE_URL: postgresql://trax9:${DB_PASSWORD:-password}@db:5432/trax9_leads
      REDIS_URL: redis://redis:6379/0
    command: >
      sh -c "alembic upgrade head &&
             uvicorn main:app --host 0.0.0.0 --port 8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx;httpx.get('http://localhost:8000/health').raise_for_status()"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  worker:
    build: { context: ./backend, dockerfile: Dockerfile }
    depends_on: [db, redis]
    env_file: [.env]
    environment:
      DATABASE_URL: postgresql://trax9:${DB_PASSWORD:-password}@db:5432/trax9_leads
      REDIS_URL: redis://redis:6379/0
    command: celery -A tasks.celery_app worker --loglevel=info --concurrency=5
             -Q profile,discovery,email_find,audit,email
    restart: unless-stopped

  beat:
    build: { context: ./backend, dockerfile: Dockerfile }
    depends_on: [redis]
    env_file: [.env]
    command: celery -A tasks.celery_app beat --loglevel=info
    restart: unless-stopped

  frontend:
    build: { context: ./frontend, dockerfile: Dockerfile }
    ports: ["3000:80"]
    depends_on: [backend]
    restart: unless-stopped

volumes:
  pgdata:
```

**File**: `backend/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app

# System deps for Playwright/Chromium
RUN apt-get update && apt-get install -y \
    wget gnupg libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 libxdamage1 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**File**: `frontend/Dockerfile`

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

**File**: `frontend/nginx.conf`

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / { try_files $uri $uri/ /index.html; }

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**File**: `backend/requirements.txt`

```
# Web
fastapi==0.110.0
uvicorn[standard]==0.27.0
python-multipart==0.0.9

# Database
sqlalchemy==2.0.27
psycopg2-binary==2.9.9
alembic==1.13.1

# Auth + secrets
python-jose[cryptography]==3.3.0
bcrypt==4.1.2
cryptography==42.0.5          # Fernet for user_settings

# HTTP + scraping
httpx==0.27.0
beautifulsoup4==4.12.3
lxml==5.1.0
playwright==1.42.0

# Queue
celery==5.3.6
redis==5.0.3

# AI
openai==1.14.3
anthropic==0.25.0

# Email
sendgrid==6.11.0
imapclient==3.0.1             # reply polling

# Config + utils
python-dotenv==1.0.1
pydantic==2.6.3
pydantic-settings==2.1.0
email-validator==2.1.0
sentry-sdk==1.44.0

# Tests
pytest==8.1.1
pytest-asyncio==0.23.6
respx==0.21.0
```

**Deployment additions:**
- `beat` service wired to the three schedules (warmup, sequences, replies).
- Backend healthcheck hits `/health`.
- Run `alembic upgrade head` on backend start (migrations now actually exist under `alembic/versions/`).
- Separate **sending subdomain/domain** for deliverability (docs): configure SPF, DKIM, DMARC on the sending domain; do NOT send from the bare corporate root domain.
- Requirements add: `cryptography`, `anthropic`, `imapclient`, `sentry-sdk`, `respx`, `pytest`, `pytest-asyncio`.

---

## 10. GOOGLE APIS SETUP

```text
=== GOOGLE PLACES API ===
1. https://console.cloud.google.com → create/select a project
2. Enable "Places API"
3. Credentials → Create Credentials → API Key
4. Restrict: API restrictions → "Places API" only
5. Copy to GOOGLE_PLACES_API_KEY
6. $200/month free credit (~40K calls); $17/1K after

=== GOOGLE CUSTOM SEARCH API ===
1. Enable "Custom Search API" in Cloud Console
2. https://programmablesearchengine.google.com/ → Add
3. "Sites to search": enter any URL, then choose "Search the entire web"
4. Create → copy the Search Engine ID (cx)
5. Create API key → restrict to "Custom Search API"
6. Set GOOGLE_CUSTOM_SEARCH_API_KEY + GOOGLE_CUSTOM_SEARCH_CX
7. 100 queries/day free, $5 per 1K after

=== GOOGLE PAGESPEED INSIGHTS API ===
1. Enable "PageSpeed Insights API" in Cloud Console
2. Create API key → restrict to "PageSpeed Insights API"
3. Set GOOGLE_PAGESPEED_API_KEY
4. Free, unlimited (reasonable rate limits)

=== OPENAI (if ai_provider=openai) ===
1. https://platform.openai.com/api-keys → create key
2. Add billing (minimum $5)
3. gpt-4o-mini: $0.15/1M input, $0.60/1M output
   ~500 tokens/email → ~$0.0001/email → 200 emails/day ≈ $0.60/month

=== SENDGRID ===
1. https://signup.sendgrid.com → free account
2. Verify sender domain (see DNS deliverability below) — NOT just a single address
3. Settings → API Keys → Create (Full Access)
4. Set SENDGRID_API_KEY
5. Free: 100 emails/day forever. Paid: $19.95/mo for 50K/mo
```

**Additional for v2:**
- **Anthropic**: platform.claude.com → API key → set as `ai_provider=anthropic`. Claude Haiku 4.5 recommended for email writing (cheap, high quality).
- **Hunter.io** (optional): dashboard → API key → per-user setting for email finding fallback.
- **SendGrid Inbound Parse** (optional, for replies): add MX on a subdomain → point to `/api/webhook/inbound`.
- **DNS deliverability**: SPF, DKIM (SendGrid domain auth), DMARC records on sending domain.

---

## 11. AI PROMPTS

### Agency Analysis (System Prompt) — NEW
```
You are a B2B positioning analyst. Given the website content of a marketing/dev
agency, extract what they sell and who they should target for outreach.

Return ONLY valid JSON (no markdown, no fences):
{
  "company_name": string,
  "services": [{"name": string, "description": string}],
  "ideal_client": {
    "industries": [string],
    "company_size": string,
    "geos": [string],
    "buying_signals": [string]   // observable signs a business needs this agency
  },
  "positioning": string,
  "suggested_keywords": [string],   // 10-20 concrete search seeds to find such businesses
  "suggested_locations": [string]
}

Agency website content:
{agency_content}
```

### Email Writing (System Prompt) — updated
```
You are Ayesha, a Client Consultant at Trax9 — a web development, SEO and digital
marketing agency (Richmond, Texas / Karachi, Pakistan).

Trax9 services you may pitch:
{agency_services}

Write a personalized cold outreach email to {company_name}.

AUDIT FINDINGS ABOUT THEIR BUSINESS:
{full_audit_context}
TOP GAP (highest scoring issue): {top_gap}

RULES (follow all):
1. Pick the ONE Trax9 service that best solves {top_gap}. Lead with that.
2. Reference SPECIFIC audit findings. Never generic.
3. Consultative, value-first. Never "we can help you grow".
4. Exactly 3-4 short paragraphs.
5. Subject line: company/industry + a specific observation.
6. One concrete improvement suggestion from a real finding.
7. Low-friction CTA: "reply to this email" or "hop on a 15-min call".
8. Sign: "Ayesha | Client Consultant, Trax9".
9. NEVER use: "I hope this email finds you well", "I came across", "touching base",
   "I wanted to reach out", "I'm reaching out", "we specialize in".
10. Warm, expert, brief, specific — like a real person who did the research.

FORMAT:
Subject: [Subject line]

[Body]

Ayesha | Client Consultant, Trax9
```

### Brand Analysis (System Prompt)
```
You are a business analyst. Analyze the following business based on their
website content. Be specific and data-driven.

Extract and return JSON ONLY (no other text) with these EXACT fields:
- industry: string (e.g. "ecommerce - fashion", "local services - restaurant")
- target_audience: string (who they seem to be selling to)
- brand_positioning: string (what makes them unique, their tone)
- website_quality_score: integer (1-10)
- strengths: array of strings (3 things they do well online)
- weaknesses: array of strings (3 things they could improve)
- estimated_size: string ("small" / "medium" / "large enterprise")
- pain_points: array of strings (problems they likely have that Trax9 could solve)
- best_services: array of strings (which Trax9 services would help them most)
- competitor_notes: string (any visible competitors mentioned)

Website Content:
{website_content}

Return ONLY valid JSON. No markdown, no code fences.
```

---

## 12. COMPLETE TOOL & API CATALOG

### Lead Discovery
| Tool | Free tier | Paid | Notes |
|---|---|---|---|
| **Google Places API** | $200/mo credit (~40K calls) | $17/1K after | best for Maps-based lead gen |
| **Google Custom Search** | 100 queries/day | $5/1K | "Shopify stores in X" |
| SerpAPI | 100 searches/mo | $50/mo for 5K | Google SERP API |
| Apify | $5 credits | $49/mo | pre-built Maps/Yelp scrapers |
| Yelp Fusion API | 5K calls/day | — | businesses by category |
| Apollo.io | 10K credits/mo | $99/mo for 18K | B2B database with emails |
| **Playwright** (custom) | free | proxy cost | most flexible |

### Website Audit
| Tool | Free | Paid | Notes |
|---|---|---|---|
| **httpx + BeautifulSoup** | free | — | page analysis, tech-stack hints |
| **Playwright** | free | — | JS rendering |
| **PageSpeed / Lighthouse API** | free unlimited | — | Core Web Vitals |
| BuiltWith | 50 lookups/mo | $295/mo | tech-stack detection |
| Wappalyzer API | 500 lookups/mo | $99/mo for 5K | tech-stack detection |

### Ad Intelligence
| Tool | Free | Paid | Notes |
|---|---|---|---|
| **Meta Ads Library** (scrape) | free | — | public, rate-limited |
| **Google Ads Transparency** (scrape) | free | — | public |
| BigSpy | limited | $79/mo | ad spy across platforms |

### SEO
| Tool | Free | Paid | Notes |
|---|---|---|---|
| **Google PageSpeed API** | free unlimited | — | Core Web Vitals |
| **Custom Python** | free | — | meta, headers, schema, images |
| Moz API | 10 queries/mo | $49/mo for 50K | domain authority, backlinks |
| Ahrefs API | — | $179/mo for 1K rows | best backlinks + keywords |
| Semrush API | 50 queries/day | $139/mo for 5K | keywords, competitors |

### AI
| Tool | Free | Paid | Notes |
|---|---|---|---|
| **Claude Haiku 4.5** | — | cheap | **recommended** — best quality/cost for emails |
| Claude Sonnet | — | higher | best quality |
| OpenAI GPT-4o-mini | — | $0.15/1M in | cheap alternative |
| OpenAI GPT-4o | — | $2.50/1M in | better quality |
| Groq | rate-limited free | pay as grow | fast open models |

### Email Sending
| Tool | Free | Paid | Notes |
|---|---|---|---|
| **SendGrid** | 100/day forever | $19.95/mo for 50K | **recommended** — reliable + webhooks |
| Amazon SES | 62K/mo from EC2 | $0.10/1K | cheapest at scale |
| Mailgun | 5K for 3 months | $35/mo for 50K | good DX |
| Brevo | 300/day | $25/mo for 20K | has CRM features |
| Google Apps Script | 100/day personal, 1500/day Workspace | $6/user/mo | Gmail-based; caps too low to scale |

### Infrastructure
| Tool | Free | Paid | Notes |
|---|---|---|---|
| **Railway** | $5 credit | $5+/mo | simplest deploy |
| Fly.io | 3 small VMs | $19/mo | easy deploy |
| **Supabase** | 500MB DB | $25/mo for 8GB | Postgres + auth + storage |
| **Upstash Redis** | 10MB | $4/mo for 50MB | serverless Redis |
| DigitalOcean | — | $12/mo VPS | full control |

### v2 additions


| Purpose | Tool | Free | Notes |
|---|---|---|---|
| Email finding | On-site scrape | Free | primary |
| Email finding | Hunter.io | 25/mo free | optional fallback |
| Social/review enrichment | SocialCrawl | 100 credits free | 44 platforms, per-credit no-sub; gate on fit_score |
| AI writing | Anthropic Claude Haiku 4.5 | pay | cheap, high quality; recommended |
| Replies | IMAP (Gmail/Workspace) | Free | poll inbox |
| Replies | SendGrid Inbound Parse | Free | MX subdomain |
| Anti-ban | Rotating proxies | ~$50-100/mo | required for scraping at scale |
| Errors | Sentry | free tier | observability |

### Recommended starter stack ($10-25/mo)
```
Profiling:  own site scrape + Claude Haiku
Discovery:  Playwright (proxied) + Google Places ($200 free credit)
Email find: on-site scrape (free)
Audit:      Python + PageSpeed API (free) + proxied Playwright for ad libraries
Enrich:     SocialCrawl (100 free credits; only high-fit leads)
AI:         Claude Haiku 4.5 (~$5-10/mo)
Sending:    SendGrid (100/day free) on an authenticated sending subdomain
Replies:    IMAP poll (free)
Proxies:    budget rotating pool ($50/mo) — the one real added cost for reliable scraping
Hosting:    Fly.io / Railway + Supabase Postgres + Upstash Redis
```

---

## 13. BUILD ORDER FOR CLAUDE

Build in this exact order; each step depends on prior.

### PHASE 1: Backend Core + Multi-tenant + Security
1. `config.py`, `database.py`, `crypto.py`
2. `models.py` (ALL tables incl. new ones), `schemas.py`
3. `alembic` init + first migration
4. `main.py` (CORS, lifespan, exception handler, `/health`)
5. `services/auth_service.py`, `deps.py` (get_current_user, get_db, get_user_settings)
6. `routers/auth.py`, `routers/settings.py` (encrypted keys)
7. `requirements.txt`, `Dockerfile`, `docker-compose.yml`

### PHASE 2: Agency Brain + Discovery + Email Finding
1. `services/ai/client.py` (OpenAI + Anthropic + token accounting)
2. `services/scraping/browser.py` + `proxies.py` (proxy/UA rotation)
3. `services/profile/agency_analyzer.py` + `routers/profile.py` + `tasks/profile_tasks.py`
4. `services/discovery/{google_maps,google_search,directory_scraper}.py`
5. `services/discovery/email_finder.py`
6. `tasks/celery_app.py`, `tasks/discovery_tasks.py`, `routers/campaigns.py`
7. Test: analyze trax9.com → auto-keywords → discover → emails found → in DB

### PHASE 3: Audit + Scoring + Social Enrichment
1. `services/audit/{website,seo,meta_ads,google_ads,brand_rnd,scoring}.py`
2. `audit_cache` reuse logic
3. `services/audit/social.py` (SocialCrawl, gated on `fit_score >= SOCIAL_ENRICH_MIN_SCORE`, re-score after)
4. `tasks/audit_tasks.py` (per-item progress, partial-save, cache, enrich gate)
5. Test: audit leads → audit_data + fit_score populated; high-fit leads enriched + re-scored; no-key = graceful skip

### PHASE 4: Email System + Compliance + Replies + Sequences
1. `services/email/writer.py` (service-match)
2. `services/email/sender.py` (Redis limits, warmup, suppression, unsubscribe footer)
3. Unsubscribe endpoint + suppression handling
4. `services/email/reply_tracker.py` + `routers/webhooks.py` (events + inbound + signature verify)
5. `services/email/sequencer.py` + `tasks/sequence_tasks.py` + beat schedules
6. `routers/leads.py` (detail, send, regenerate, export)
7. Test: send → open tracked → reply cancels sequence → unsubscribe suppresses

### PHASE 5: Frontend
1. Vite + Tailwind + router + `AuthContext` + `api/client.js`
2. Login, Onboarding (agency analyze), Layout/Sidebar/Header
3. Dashboard (stats + chart), Campaigns + create modal
4. CampaignDetail (leads table w/ fit score + needs-email), action buttons + progress
5. LeadDetail (audit tabs incl. Google Ads, email preview, sequence timeline)
6. Settings (keys, AI provider, IMAP, warmup, address, quota)

### PHASE 6: Observability, Tests, Deploy
1. Structured logging + request-id + Sentry
2. `pytest` suite (mock external calls)
3. Warmup beat + reply poll beat verified
4. DNS deliverability (SPF/DKIM/DMARC) on sending subdomain
5. Deploy via docker-compose (Fly.io/Railway) with healthchecks + `alembic upgrade head`

---

## End of PRD v2

Everything needed to build the Trax9 AI Lead Generation & Outreach System as a real, multi-tenant, compliant, anti-ban AI SaaS. Build each file in the order under §13, starting with backend core.
```
