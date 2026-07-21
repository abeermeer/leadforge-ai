"""Celery tasks: campaign lead discovery fan-out (queue: discovery). PRD §5.

run_campaign_discovery is the master: it creates the campaign's Task row and
builds a chord — header = one task per keyword x location for Google Maps and
Google Custom Search (plus Yelp per location for the FIRST keyword only, to
limit scraping load), callback = save_discovered_leads (dedup + insert + mark
the Task row complete + kick off email finding).

Header tasks NEVER raise: a failed source logs and returns [] so a dead item
can never stick the chord (PRD §5). Google API keys arrive decrypted from the
enqueuing request handler (routers/campaigns.py). find_emails_task reads the
optional Hunter key itself (it is enqueued worker-side with no request
context); crypto.decrypt_dict never raises.

save_leads_for_campaign / create_discovery_task_row / complete_discovery_task_row
are plain functions shared with routers/campaigns.py so the synchronous
fallback path (Redis down) reuses the exact same dedup + insert + bookkeeping.
"""
import asyncio
import logging
import uuid
from datetime import datetime

from celery import chord
from sqlalchemy.orm import Session

from config import settings
from crypto import decrypt_dict
from database import SessionLocal
from models import (
    Campaign,
    EmailSource,
    Lead,
    LeadStatus,
    Task,
    TaskStatus,
    TaskType,
    UsageCounter,
    UserSettings,
)
from services.discovery import normalize_domain
from services.discovery.directory_scraper import scrape_directory
from services.discovery.email_finder import find_email
from services.discovery.google_maps import search_places
from services.discovery.google_search import search_google
from tasks.celery_app import celery_app

logger = logging.getLogger("trax9.tasks.discovery")


# ------------------------------------------------------------------ shared helpers
# Plain functions (not tasks) so routers/campaigns.py can run the same logic
# inline when the Celery broker is unreachable.


def create_discovery_task_row(db: Session, campaign: Campaign, total_items: int) -> Task:
    """Create the running Task row that tracks this discovery fan-out."""
    row = Task(
        user_id=campaign.user_id,
        campaign_id=campaign.id,
        type=TaskType.discovery,
        status=TaskStatus.running,
        total_items=total_items,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def complete_discovery_task_row(db: Session, campaign_id: uuid.UUID) -> None:
    """Mark the campaign's newest running discovery Task row completed."""
    row = (
        db.query(Task)
        .filter(
            Task.campaign_id == campaign_id,
            Task.type == TaskType.discovery,
            Task.status == TaskStatus.running,
        )
        .order_by(Task.created_at.desc())
        .first()
    )
    if row is None:
        return
    row.completed_items = row.total_items
    row.status = TaskStatus.completed
    db.commit()


def save_leads_for_campaign(db: Session, campaign: Campaign, batches: list) -> int:
    """Flatten per-source result batches into new Lead rows for the campaign.

    Deduplicates on normalize_domain(website) against both the incoming batch
    and the campaign's existing leads, caps inserts at
    settings.MAX_LEADS_PER_DISCOVERY, and inserts with status='discovered'.
    Returns the number of new leads inserted.
    """
    existing: set[str] = {
        normalize_domain(website)
        for (website,) in db.query(Lead.website).filter(Lead.campaign_id == campaign.id)
    }

    new_count = 0
    for batch in batches or []:
        if not isinstance(batch, list):
            continue  # a failed header task may report None/garbage — skip it
        for item in batch:
            if not isinstance(item, dict):
                continue
            domain = normalize_domain(item.get("website") or "")
            if not domain or domain in existing:
                continue
            if new_count >= settings.MAX_LEADS_PER_DISCOVERY:
                logger.warning(
                    "campaign %s: discovery cap %d reached, dropping remainder",
                    campaign.id,
                    settings.MAX_LEADS_PER_DISCOVERY,
                )
                db.commit()
                return new_count
            existing.add(domain)
            db.add(
                Lead(
                    user_id=campaign.user_id,
                    campaign_id=campaign.id,
                    company_name=(item.get("company_name") or "Unknown")[:255],
                    website=domain,
                    phone=(item.get("phone") or None),
                    address=(item.get("address") or None),
                    city=(item.get("city") or None),
                    country=(item.get("country") or None),
                    category=(item.get("category") or None),
                    source=(item.get("source") or None),
                    status=LeadStatus.discovered,
                )
            )
            new_count += 1

    db.commit()
    return new_count


def _record_places_call(db: Session, campaign_id: str) -> None:
    """Upsert usage_counters for the current 'YYYY-MM', increment places_calls."""
    campaign = db.get(Campaign, uuid.UUID(campaign_id))
    if campaign is None:
        return
    period = datetime.utcnow().strftime("%Y-%m")
    row = (
        db.query(UsageCounter)
        .filter(UsageCounter.user_id == campaign.user_id, UsageCounter.period == period)
        .first()
    )
    if row is None:
        row = UsageCounter(user_id=campaign.user_id, period=period, places_calls=0)
        db.add(row)
    row.places_calls = (row.places_calls or 0) + 1
    db.commit()


# ------------------------------------------------------------------ header tasks


@celery_app.task
def discover_google_maps_task(
    campaign_id: str, keyword: str, location: str, api_key: str | None
) -> list[dict]:
    """Places Text Search for one keyword x location. Never raises."""
    db = SessionLocal()
    try:
        leads = asyncio.run(search_places(keyword, location, api_key=api_key))
        try:
            _record_places_call(db, campaign_id)
        except Exception:
            logger.exception("failed to record places_calls usage for %s", campaign_id)
        return leads
    except Exception:
        logger.exception(
            "google_maps discovery failed for '%s' in '%s' (campaign %s)",
            keyword,
            location,
            campaign_id,
        )
        return []
    finally:
        db.close()


@celery_app.task
def discover_google_search_task(
    campaign_id: str, keyword: str, location: str, api_key: str | None, cx: str | None
) -> list[dict]:
    """Custom Search for one keyword x location. Never raises."""
    try:
        return asyncio.run(search_google(keyword, location, api_key=api_key, cx=cx))
    except Exception:
        logger.exception(
            "google_search discovery failed for '%s' in '%s' (campaign %s)",
            keyword,
            location,
            campaign_id,
        )
        return []


@celery_app.task
def discover_directory_task(
    campaign_id: str, directory: str, keyword: str, location: str
) -> list[dict]:
    """Directory (Yelp/Yellow Pages) scrape for one keyword x location. Never raises."""
    try:
        return asyncio.run(scrape_directory(directory, keyword, location))
    except Exception:
        logger.exception(
            "%s discovery failed for '%s' in '%s' (campaign %s)",
            directory,
            keyword,
            location,
            campaign_id,
        )
        return []


# ------------------------------------------------------------------ email finding


@celery_app.task
def find_emails_task(campaign_id: str) -> dict:
    """Find contact emails for the campaign's discovered leads without one.

    Each lead is flipped to status='finding_email' while find_email runs, then
    restored to 'discovered' whatever happens — email finding must never strand
    a lead in a transient status. Per-lead failures are logged and skipped.
    """
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, uuid.UUID(campaign_id))
        if campaign is None:
            logger.warning("find_emails_task: campaign %s not found", campaign_id)
            return {"leads_processed": 0, "emails_found": 0}

        # Optional per-user Hunter key. Decrypted here because this task is
        # enqueued worker-side (chord callback) with no request context.
        settings_row = (
            db.query(UserSettings).filter(UserSettings.user_id == campaign.user_id).first()
        )
        hunter_key = (
            decrypt_dict(settings_row.encrypted_keys).get("hunter") if settings_row else None
        )

        leads = (
            db.query(Lead)
            .filter(
                Lead.campaign_id == campaign.id,
                Lead.email.is_(None),
                Lead.status == LeadStatus.discovered,
            )
            .all()
        )

        found = 0
        for lead in leads:
            lead.status = LeadStatus.finding_email
            db.commit()
            try:
                result = asyncio.run(find_email(lead.company_name, lead.website, hunter_key))
                if result.get("email"):
                    lead.email = result["email"]
                    lead.email_source = EmailSource(result["source"])
                    lead.email_confidence = result.get("confidence")
                    found += 1
            except Exception:
                logger.exception("find_email failed for lead %s (%s)", lead.id, lead.website)
            finally:
                lead.status = LeadStatus.discovered
                db.commit()

        logger.info(
            "find_emails_task campaign %s: %d/%d emails found", campaign_id, found, len(leads)
        )
        return {"leads_processed": len(leads), "emails_found": found}
    finally:
        db.close()


# ------------------------------------------------------------------ chord callback


@celery_app.task
def save_discovered_leads(results: list, campaign_id: str) -> dict:
    """Chord callback: dedup + insert all source batches, close out the Task row.

    `results` is the list of header task return values (one list of lead dicts
    per source task). After saving, enqueues find_emails_task for the new leads.
    """
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, uuid.UUID(campaign_id))
        if campaign is None:
            logger.warning("save_discovered_leads: campaign %s not found", campaign_id)
            return {"new_leads": 0}

        new_leads = save_leads_for_campaign(db, campaign, results or [])
        complete_discovery_task_row(db, campaign.id)

        try:
            find_emails_task.delay(campaign_id)
        except Exception:
            logger.exception("could not enqueue find_emails_task for campaign %s", campaign_id)

        logger.info("campaign %s discovery saved %d new leads", campaign_id, new_leads)
        return {"new_leads": new_leads}
    finally:
        db.close()


# ------------------------------------------------------------------ master task


@celery_app.task
def run_campaign_discovery(campaign_id: str, keys: dict) -> dict:
    """Fan out discovery for a campaign as a chord (PRD §5).

    Header: Google Maps + Google Custom Search per keyword x location, plus a
    Yelp directory scrape per location for the FIRST keyword only (directories
    ban fast — keep the load light). Callback: save_discovered_leads.

    `keys` is the decrypted subset {google_places, google_custom_search,
    google_custom_search_cx} passed by the enqueuing request handler.
    """
    db = SessionLocal()
    try:
        campaign = db.get(Campaign, uuid.UUID(campaign_id))
        if campaign is None:
            logger.warning("run_campaign_discovery: campaign %s not found", campaign_id)
            return {"error": "campaign not found"}

        keywords = [k for k in (campaign.seed_keywords or []) if k]
        locations = [loc for loc in (campaign.target_locations or []) if loc]
        if not keywords or not locations:
            row = Task(
                user_id=campaign.user_id,
                campaign_id=campaign.id,
                type=TaskType.discovery,
                status=TaskStatus.failed,
                error="Campaign has no seed keywords or target locations",
            )
            db.add(row)
            db.commit()
            return {"error": "campaign has no keywords or locations"}

        # total_items counts the maps+search grid; Yelp tasks are extra load
        # on top, not user-visible progress units.
        task_row = create_discovery_task_row(
            db, campaign, total_items=len(keywords) * len(locations) * 2
        )

        header = []
        for keyword in keywords:
            for location in locations:
                header.append(
                    discover_google_maps_task.s(
                        campaign_id, keyword, location, keys.get("google_places")
                    )
                )
                header.append(
                    discover_google_search_task.s(
                        campaign_id,
                        keyword,
                        location,
                        keys.get("google_custom_search"),
                        keys.get("google_custom_search_cx"),
                    )
                )
        for location in locations:
            header.append(discover_directory_task.s(campaign_id, "yelp", keywords[0], location))

        chord(header)(save_discovered_leads.s(campaign_id))
        return {"task_id": str(task_row.id), "header_tasks": len(header)}
    finally:
        db.close()
