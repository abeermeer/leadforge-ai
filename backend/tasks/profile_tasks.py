"""Celery task: background agency profiling (queue: profile).

celery_app routes tasks.profile_tasks.* to the 'profile' queue. The API key is
passed in decrypted by the enqueuing request handler (deps.get_decrypted_keys)
so the worker never touches Fernet material.
"""
import asyncio
import uuid

from database import SessionLocal
from services.profile.agency_analyzer import analyze_agency
from tasks.celery_app import celery_app


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30)
def analyze_agency_task(self, user_id: str, website: str, provider: str, api_key: str) -> dict:
    """Run analyze_agency in a worker; retry once on failure.

    Returns a small JSON-safe summary for the orchestrator/task inspector.
    """
    db = SessionLocal()
    try:
        profile = asyncio.run(
            analyze_agency(
                website, uuid.UUID(user_id), db, provider=provider, api_key=api_key
            )
        )
        return {
            "profile_id": str(profile.id),
            "website": profile.website,
            "company_name": profile.company_name,
        }
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()
