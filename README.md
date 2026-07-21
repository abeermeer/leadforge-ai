# LeadForge AI

**AI agents that find businesses, audit their marketing, write personalized cold outreach, and send it** — with open/reply tracking, CAN-SPAM compliance, warmup, and automatic follow-ups. Built for the Trax9 agency.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Celery](https://img.shields.io/badge/Celery-5.3-37814A?logo=celery&logoColor=white)
![Tests](https://img.shields.io/badge/tests-16%20passing-brightgreen)
![License](https://img.shields.io/badge/license-proprietary-lightgrey)

## How it works

Paste your agency's website. The **Agency Brain** reads it, learns what you sell and who your ideal client is, then a pipeline of AI agents does the rest:

| Agent | Job |
|---|---|
| 🧠 Brain | learns your services + ideal client profile from your own site |
| 🔍 Discover | finds matching businesses (Google Maps, Search, directories) |
| ✉️ Email Find | locates a real inbox (site scrape → pattern → Hunter) |
| 🩻 Audit | x-rays their site, SEO, Meta ads, Google ads |
| 📊 Score | ranks every lead 0–100 against your ideal client |
| ✨ Enrich | adds reviews + social intel (high-fit leads only) |
| ✍️ Write | drafts an opener citing one real finding, pitching one matching service |
| 🚀 Send | delivers with warmup, rate limits, unsubscribe, tracking, follow-ups |

Everything is visible live in a mission-control dashboard, styled to the Trax9 brand.

## Run it for real (not demo)

The system runs today with **zero external keys** (discovery/audit/send just no-op or degrade). To make it *actually* find leads and send email, add real API keys in **Settings** (per-user, encrypted at rest) or seed them via `.env`.

### 1. Backend
```bash
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp ../.env.example ../.env          # then edit .env
.venv/Scripts/alembic upgrade head  # create tables
.venv/Scripts/uvicorn main:app --reload
```

### 2. Frontend
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173  (proxies /api -> :8000)
```

### 3. Full stack + workers (Redis-backed queue, warmup, replies, follow-ups)
```bash
docker compose up   # db, redis, backend, worker, beat, frontend
```
Without Redis the API still works — discovery/audit/send fall back to **inline** execution (capped), so you can demo end-to-end on one machine. With Redis, work fans out to Celery + beat runs warmup ramp / follow-ups / reply polling.

## Keys you add (all optional, all per-user in Settings)

| Key | Enables | Get it |
|---|---|---|
| `anthropic` (or `openai`) | agency brain, brand R&D, email writing | platform.claude.com / platform.openai.com |
| `google_places` | Google Maps discovery | console.cloud.google.com → Places API |
| `google_custom_search` + `_cx` | Google Search discovery | programmablesearchengine.google.com |
| `google_pagespeed` | SEO / Core Web Vitals audit | Cloud Console → PageSpeed API |
| `sendgrid` | sending + open/bounce tracking | sendgrid.com (100/day free) |
| `hunter` | email-finding fallback | hunter.io |
| `socialcrawl` | review + social enrichment | socialcrawl.dev |
| `imap_password` + host/user | reply detection | your inbox |

Scraping (Meta Ads Library, Google Ads Transparency, Yelp) needs **no key** but wants a proxy pool (`PROXY_POOL` in `.env`) to avoid IP bans at scale.

## Email deliverability (required before sending real volume)

Send from a **subdomain** (e.g. `out.trax9.com`), never the bare corporate domain. On that sending domain configure:
- **SPF**: `v=spf1 include:sendgrid.net ~all`
- **DKIM**: SendGrid → Sender Authentication → Authenticate Your Domain (adds CNAME records)
- **DMARC**: `v=DMARC1; p=quarantine; rua=mailto:dmarc@trax9.com`

Every email already carries a one-click unsubscribe link + `List-Unsubscribe` header + your postal address (set it in Settings — legally required). Bounces and spam reports auto-suppress. Warmup ramps 10→cap over days.

## Pipeline

```
Agency Brain → Discover → Email Find → Audit → Score → Enrich → Write → Send → Track → Follow-up
```
Each stage writes back before the next. Replies/unsubscribes/bounces cancel remaining follow-ups.

## Health & ops
- `GET /health` — DB + Redis liveness (Docker healthcheck)
- `X-Request-ID` on every response; Sentry via `SENTRY_DSN`
- `pytest` — 16 gate tests across all phases (external calls mocked)

## Deploy
Railway / Fly.io / any Docker host. Point `DATABASE_URL` at managed Postgres, `REDIS_URL` at Upstash/managed Redis, set `FERNET_KEY` (generate: `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`) and `SECRET_KEY`. Backend command runs `alembic upgrade head` on boot.
