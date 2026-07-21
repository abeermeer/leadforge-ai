"""Celery tasks: per-lead audit + scoring + social enrichment (queue: audit).

audit_campaign is the master: it creates the campaign's audit Task row and builds
a chord — header = one audit_single_lead per eligible lead, callback = mark the
Task row complete.

Each lead runs the full intelligence pipeline in _audit_one (a plain function so
routers/campaigns.py can reuse it inline when the Celery broker is down):

    website -> seo -> meta ads -> google ads -> brand R&D   (each try/except;
        a failing sub-audit is recorded under audit_data['errors'][name] and the
        partial audit is still saved)
    -> score_lead (fit_score + reasons)
    -> if fit_score >= user's social_enrich_min_score AND a SocialCrawl key is
       configured: enrich_social, merge into audit_data['social'], re-score

audit_cache short-circuits re-fetching a domain audited in the last 7 days (the
lead is still re-scored against the current agency profile). Brand-analysis
tokens and SocialCrawl credits are metered into usage_counters.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from celery import chord
from sqlalchemy.orm import Session

from crypto import decrypt_dict
from database import SessionLocal
from models import (
    AgencyProfile,
    AuditCache,
    Campaign,
    Lead,
    LeadStatus,
    Task,
    TaskStatus,
    TaskType,
    UsageCounter,
    UserSettings,
)
from services.ai.client import record_ai_usage
from services.audit.brand_rnd import analyze_brand
from services.audit.google_ads import audit_google_ads
from services.audit.meta_ads import audit_meta_ads
from services.audit.scoring import score_lead
from services.audit.seo import audit_seo
from services.audit.social import enrich_social
from services.audit.website import audit_website
from services.discovery import normalize_domain
from tasks.celery_app import celery_app

logger = logging.getLogger("trax9.tasks.audit")

AUDIT_CACHE_TTL = timedelta(days=7)

# Leads in these statuses are (re)audited when a campaign audit runs.
AUDITABLE_STATUSES = (LeadStatus.discovered, LeadStatus.audited, LeadStatus.failed)


# ------------------------------------------------------------------ helpers


def _record_socialcrawl_credits(db: Session, user_id, credits: int) -> None:
    """Upsert usage_counters for the current 'YYYY-MM', add SocialCrawl credits."""
    if not credits:
        return
    period = datetime.utcnow().strftime("%Y-%m")
    row = (
        db.query(UsageCounter)
        .filter(UsageCounter.user_id == user_id, UsageCounter.period == period)
        .first()
    )
    if row is None:
        row = UsageCounter(user_id=user_id, period=period, socialcrawl_credits=0)
        db.add(row)
    row.socialcrawl_credits = (row.socialcrawl_credits or 0) + int(credits)
    db.commit()


def _ideal_client(db: Session, user_id) -> dict | None:
    """The user's latest agency profile ideal_client, if any."""
    profile = (
        db.query(AgencyProfile)
        .filter(AgencyProfile.user_id == user_id)
        .order_by(AgencyProfile.updated_at.desc())
        .first()
    )
    return profile.ideal_client if profile else None


def _cached_audit(db: Session, domain: str) -> dict | None:
    """Return fresh (<7d) cached audit_data for a domain, else None."""
    if not domain:
        return None
    row = db.query(AuditCache).filter(AuditCache.domain == domain).first()
    if row is None or row.audit_data is None:
        return None
    if datetime.utcnow() - row.fetched_at > AUDIT_CACHE_TTL:
        return None
    return dict(row.audit_data)


def _upsert_audit_cache(db: Session, domain: str, audit_data: dict) -> None:
    if not domain:
        return
    row = db.query(AuditCache).filter(AuditCache.domain == domain).first()
    if row is None:
        row = AuditCache(domain=domain)
        db.add(row)
    row.audit_data = audit_data
    row.fetched_at = datetime.utcnow()
    db.commit()


def _bump_audit_progress(db: Session, campaign_id) -> None:
    """Increment completed_items on the campaign's running audit Task row."""
    row = (
        db.query(Task)
        .filter(
            Task.campaign_id == campaign_id,
            Task.type == TaskType.audit,
            Task.status == TaskStatus.running,
        )
        .order_by(Task.created_at.desc())
        .first()
    )
    if row is None:
        return
    row.completed_items = (row.completed_items or 0) + 1
    db.commit()


def _run(coro):
    """Run one async audit call to completion (each task/thread is sync)."""
    return asyncio.run(coro)


# ------------------------------------------------------------------ core per-lead


def _audit_one(db: Session, lead: Lead, keys: dict) -> dict:
    """Full audit + score + optional enrichment for a single lead.

    `keys` = {provider, ai_key, google_pagespeed, socialcrawl}. Mutates and
    commits the lead. Returns a small summary dict. Never raises — a total
    failure marks the lead 'failed' and returns {'status': 'failed'}.
    """
    domain = normalize_domain(lead.website or "")
    provider = keys.get("provider") or "anthropic"
    ai_key = keys.get("ai_key")

    try:
        cached = _cached_audit(db, domain)
        if cached is not None:
            audit_data = cached
            logger.info("lead %s: reusing cached audit for %s", lead.id, domain)
        else:
            lead.status = LeadStatus.auditing
            db.commit()
            audit_data = {"errors": {}}

            def _sub(name, coro):
                try:
                    return _run(coro)
                except Exception as exc:  # one sub-audit failing must not sink the rest
                    logger.warning("lead %s: %s audit failed: %s", lead.id, name, exc)
                    audit_data["errors"][name] = str(exc)
                    return None

            website = _sub("website", audit_website(lead.website))
            audit_data["website"] = website or {}
            audit_data["seo"] = (
                _sub("seo", audit_seo(lead.website, website, keys.get("google_pagespeed"))) or {}
            )
            audit_data["meta_ads"] = _sub("meta_ads", audit_meta_ads(lead.company_name, lead.website)) or {}
            audit_data["google_ads"] = (
                _sub("google_ads", audit_google_ads(lead.company_name, lead.website)) or {}
            )

            if ai_key:
                try:
                    brand, tokens = _run(
                        analyze_brand(lead.website, audit_data, provider=provider, api_key=ai_key)
                    )
                    audit_data["brand"] = brand
                    record_ai_usage(db, lead.user_id, tokens)
                except Exception as exc:
                    logger.warning("lead %s: brand analysis failed: %s", lead.id, exc)
                    audit_data["errors"]["brand"] = str(exc)

            _upsert_audit_cache(db, domain, audit_data)

        # Persist audit_data + move to audited
        lead.audit_data = audit_data
        lead.status = LeadStatus.audited
        db.commit()

        # Score
        ideal = _ideal_client(db, lead.user_id)
        scored = score_lead(_lead_dict(lead), audit_data, ideal)
        lead.fit_score = scored["fit_score"]
        lead.score_reasons = scored["score_reasons"]
        lead.status = LeadStatus.scored
        db.commit()

        # Optional SocialCrawl enrichment, gated on fit_score + key
        socialcrawl_key = keys.get("socialcrawl")
        threshold = _enrich_threshold(db, lead.user_id)
        if socialcrawl_key and lead.fit_score is not None and lead.fit_score >= threshold:
            lead.status = LeadStatus.enriching
            db.commit()
            try:
                social = _run(enrich_social(_lead_dict(lead), audit_data, socialcrawl_key))
                audit_data["social"] = social
                lead.audit_data = audit_data
                _record_socialcrawl_credits(db, lead.user_id, social.get("credits_used", 0))
                # PRD 3B.6 feedback loop: re-score with social signals in hand
                rescored = score_lead(_lead_dict(lead), audit_data, ideal)
                lead.fit_score = rescored["fit_score"]
                lead.score_reasons = rescored["score_reasons"]
            except Exception as exc:
                logger.warning("lead %s: social enrichment failed: %s", lead.id, exc)
                audit_data.setdefault("errors", {})["social"] = str(exc)
                lead.audit_data = audit_data
            lead.status = LeadStatus.enriched
            db.commit()

        return {"lead_id": str(lead.id), "status": lead.status.value, "fit_score": lead.fit_score}
    except Exception:
        logger.exception("audit pipeline failed for lead %s", lead.id)
        try:
            lead.status = LeadStatus.failed
            db.commit()
        except Exception:
            db.rollback()
        return {"lead_id": str(lead.id), "status": "failed"}


def _enrich_threshold(db: Session, user_id) -> int:
    row = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if row and row.social_enrich_min_score is not None:
        return row.social_enrich_min_score
    return 60


def _lead_dict(lead: Lead) -> dict:
    """Plain dict view of a lead for the pure scoring/enrichment functions."""
    return {
        "id": str(lead.id),
        "company_name": lead.company_name,
        "website": lead.website,
        "email": lead.email,
        "city": lead.city,
        "country": lead.country,
        "category": lead.category,
    }


# ------------------------------------------------------------------ tasks


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30)
def audit_single_lead(self, lead_id: str, keys: dict) -> dict:
    """Audit one lead, then bump the campaign's audit progress counter."""
    db = SessionLocal()
    try:
        lead = db.get(Lead, uuid.UUID(lead_id))
        if lead is None:
            return {"lead_id": lead_id, "status": "missing"}
        result = _audit_one(db, lead, keys)
        _bump_audit_progress(db, lead.campaign_id)
        return result
    finally:
        db.close()


@celery_app.task
def complete_audit_task(results: list, campaign_id: str) -> dict:
    """Chord callback: mark the campaign's running audit Task row completed."""
    db = SessionLocal()
    try:
        row = (
            db.query(Task)
            .filter(
                Task.campaign_id == uuid.UUID(campaign_id),
                Task.type == TaskType.audit,
                Task.status == TaskStatus.running,
            )
            .order_by(Task.created_at.desc())
            .first()
        )
        if row is not None:
            row.status = TaskStatus.completed
            row.completed_items = row.total_items
            db.commit()
        return {"audited": len(results or [])}
    finally:
        db.close()


def create_audit_task_row(db: Session, campaign: Campaign, total_items: int) -> Task:
    row = Task(
        user_id=campaign.user_id,
        campaign_id=campaign.id,
        type=TaskType.audit,
        status=TaskStatus.running,
        total_items=total_items,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def eligible_audit_leads(db: Session, campaign: Campaign) -> list[Lead]:
    return (
        db.query(Lead)
        .filter(Lead.campaign_id == campaign.id, Lead.status.in_(AUDITABLE_STATUSES))
        .all()
    )


@celery_app.task
def audit_campaign(campaign_id: str, keys: dict) -> dict:
    """Fan out audits for all eligible leads in a campaign as a chord."""
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, uuid.UUID(campaign_id))
        if campaign is None:
            return {"error": "campaign not found"}

        leads = eligible_audit_leads(db, campaign)
        if not leads:
            return {"audited": 0, "detail": "no eligible leads"}

        create_audit_task_row(db, campaign, total_items=len(leads))
        header = [audit_single_lead.s(str(lead.id), keys) for lead in leads]
        chord(header)(complete_audit_task.s(campaign_id))
        return {"queued": len(header)}
    finally:
        db.close()
