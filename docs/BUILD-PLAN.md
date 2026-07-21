# Trax9 AI Lead Gen — Build Plan (phased tasks)

## Context
Greenfield build. Spec = `trax9-ai-lead-gen-prd-v2.md`. Repo currently holds only PRD files, no code.
User wants the full task/phase list visible up front, then triggers each task one-by-one ("start"),
executed in order. Nothing runs until user says go.

Stack: FastAPI + Postgres + Celery/Redis + React(Vite+Tailwind) + SendGrid + OpenAI/Anthropic + Playwright + SocialCrawl.

Rule per task: build files → self-check imports/syntax → report done → wait for next "start".

---

## PHASE 1 — Backend Core (multi-tenant + security foundation)
- **1.1** Scaffold repo (`backend/`, `frontend/` dirs, `.env.example`, `.gitignore`)
- **1.2** `config.py` (global env) + `database.py` (engine/session/Base) + `crypto.py` (Fernet)
- **1.3** `models.py` — ALL tables (users, user_settings, agency_profiles, campaigns, leads, audit_cache, email_logs, sequence_steps, suppressions, tasks, usage_counters) + indexes
- **1.4** `schemas.py` (Pydantic) + `deps.py` (get_db, get_current_user, get_user_settings)
- **1.5** `alembic` init + first migration
- **1.6** `main.py` (CORS, lifespan, exception handler, `/health`)
- **1.7** `services/auth_service.py` (JWT+bcrypt) + `routers/auth.py` + `routers/settings.py` (encrypted keys)
- **1.8** `requirements.txt` + `Dockerfile` + `docker-compose.yml`
- **Gate:** `docker-compose up` boots, `/health` 200, register+login works

## PHASE 2 — Agency Brain + Discovery + Email Finding
- **2.1** `services/ai/client.py` (OpenAI+Anthropic, token accounting)
- **2.2** `services/scraping/{browser,proxies}.py` (proxy + UA rotation)
- **2.3** `services/profile/agency_analyzer.py` + `routers/profile.py` + `tasks/profile_tasks.py`
- **2.4** `services/discovery/{google_maps,google_search,directory_scraper}.py`
- **2.5** `services/discovery/email_finder.py`
- **2.6** `tasks/celery_app.py` + `tasks/discovery_tasks.py` + `routers/campaigns.py`
- **Gate:** analyze trax9.com → auto keywords → discover leads → emails found → rows in DB

## PHASE 3 — Audit + Scoring + Social Enrichment
- **3.1** `services/audit/{website,seo}.py`
- **3.2** `services/audit/{meta_ads,google_ads}.py` (proxied Playwright)
- **3.3** `services/audit/brand_rnd.py` + `audit_cache` reuse
- **3.4** `services/audit/scoring.py` (fit score 0-100)
- **3.5** `services/audit/social.py` (SocialCrawl, gated `fit_score>=60`, re-score after)
- **3.6** `tasks/audit_tasks.py` (per-item progress, partial-save, cache, enrich gate)
- **Gate:** audit leads → audit_data + fit_score set; high-fit enriched+re-scored; no-key = graceful skip

## PHASE 4 — Email System + Compliance + Replies + Sequences
- **4.1** `services/email/writer.py` (service-match + social signals)
- **4.2** `services/email/sender.py` (Redis limits, warmup, suppression, unsubscribe footer + List-Unsubscribe)
- **4.3** Unsubscribe endpoint (`/api/u/{token}`) + suppression handling
- **4.4** `services/email/reply_tracker.py` + `routers/webhooks.py` (events + inbound + signature verify + dedup)
- **4.5** `services/email/sequencer.py` + `tasks/sequence_tasks.py` + beat schedules (warmup, replies, follow-ups)
- **4.6** `routers/leads.py` (detail, send, regenerate, CSV export)
- **Gate:** send → open tracked → reply cancels sequence → unsubscribe suppresses

## PHASE 5 — Frontend (React + Vite + Tailwind)
- **5.1** Vite+Tailwind+router+`AuthContext`+`api/client.js`
- **5.2** Login + Onboarding (agency analyze) + Layout/Sidebar/Header
- **5.3** Dashboard (stats + activity chart) + Campaigns list + create modal
- **5.4** CampaignDetail (leads table: fit score, needs-email; action buttons; progress bar)
- **5.5** LeadDetail (audit tabs: Website/SEO/MetaAds/GoogleAds/Brand/Social; email preview; sequence timeline)
- **5.6** Settings (keys, AI provider, SocialCrawl key+slider, IMAP, warmup, address, quota)
- **Gate:** full flow clickable end-to-end against backend

## PHASE 6 — Observability, Tests, Deploy
- **6.1** Structured logging + request-id + Sentry hook
- **6.2** `pytest` suite (discovery dedup, email_finder, audit tech-detect, sender limits) — mock external
- **6.3** Verify beat jobs (warmup ramp, reply poll)
- **6.4** DNS deliverability docs (SPF/DKIM/DMARC on sending subdomain)
- **6.5** Deploy config (Fly.io/Railway) + healthchecks + `alembic upgrade head`
- **Gate:** tests green, deploy boots

---

## Execution protocol
- User says "start" → I do the next uncompleted task (smallest labeled unit, e.g. 1.1), report, stop.
- User can say "start phase 1" to run a whole phase, or "start 2.3" to jump.
- No external API keys needed until Phase 2+; stub/skip live calls, keep functions callable.
- Each phase ends at its **Gate** — I confirm the gate before moving on.

## Open decisions (answer anytime, defaults assumed if silent)
- DB: **Postgres** (default) vs SQLite for local dev
- AI provider default: **Anthropic Claude Haiku** (cheaper/better) vs OpenAI
- Reply tracking: **IMAP poll** (default, works w/ Gmail) vs SendGrid Inbound Parse
- Deploy target: **Railway** (default) vs Fly.io vs DigitalOcean
