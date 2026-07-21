"""Lead detail, edit, delete (PRD §3F).

All endpoints scoped to current_user.id via Lead.user_id — a lead owned by
another tenant is a 404, never a 403.
"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from deps import get_current_user, get_db, get_decrypted_keys, get_user_settings
from models import AgencyProfile, EmailLog, Lead, LeadStatus, SequenceStep, User, UserSettings
from schemas import LeadDetailOut, LeadUpdate
from services.ai.client import record_ai_usage
from services.email.sender import send_email
from services.email.sequencer import schedule_followups
from services.email.writer import generate_email

router = APIRouter()


def _ai_key(settings_row: UserSettings, keys: dict) -> tuple[str, str]:
    provider = (
        settings_row.ai_provider.value
        if hasattr(settings_row.ai_provider, "value")
        else settings_row.ai_provider
    )
    return provider, keys.get(provider)


def _agency_services(db: Session, user_id) -> list:
    p = (
        db.query(AgencyProfile)
        .filter(AgencyProfile.user_id == user_id)
        .order_by(AgencyProfile.updated_at.desc())
        .first()
    )
    return (p.services or []) if p else []


def _get_lead(db: Session, user: User, lead_id: uuid.UUID) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == user.id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/leads/{lead_id}", response_model=LeadDetailOut)
def get_lead(
    lead_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _get_lead(db, user, lead_id)


@router.put("/leads/{lead_id}", response_model=LeadDetailOut)
def update_lead(
    lead_id: uuid.UUID,
    body: LeadUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a lead's email address or generated email copy."""
    lead = _get_lead(db, user, lead_id)
    for field in ("email", "email_subject", "email_body", "company_name"):
        value = getattr(body, field)
        if value is not None:
            setattr(lead, field, value)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/leads/{lead_id}/regenerate", response_model=LeadDetailOut)
def regenerate_email(
    lead_id: uuid.UUID,
    user: User = Depends(get_current_user),
    keys: dict = Depends(get_decrypted_keys),
    settings_row: UserSettings = Depends(get_user_settings),
    db: Session = Depends(get_db),
):
    """Regenerate the outreach email from the lead's audit data (no send)."""
    lead = _get_lead(db, user, lead_id)
    provider, ai_key = _ai_key(settings_row, keys)
    if not ai_key:
        raise HTTPException(status_code=400, detail="Configure your AI API key in Settings first")
    try:
        email, tokens = asyncio.run(
            generate_email(
                {"company_name": lead.company_name, "website": lead.website},
                lead.audit_data or {},
                _agency_services(db, user.id),
                provider=provider,
                api_key=ai_key,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Email generation failed: {exc}")
    lead.email_subject = email["subject"]
    lead.email_body = email["body"]
    lead.status = LeadStatus.written
    record_ai_usage(db, user.id, tokens)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/leads/{lead_id}/send")
def send_lead_email(
    lead_id: uuid.UUID,
    user: User = Depends(get_current_user),
    keys: dict = Depends(get_decrypted_keys),
    settings_row: UserSettings = Depends(get_user_settings),
    db: Session = Depends(get_db),
):
    """Generate (if needed) and send this lead's email; schedule follow-ups on success."""
    lead = _get_lead(db, user, lead_id)
    sendgrid_key = keys.get("sendgrid")
    if not sendgrid_key:
        raise HTTPException(status_code=400, detail="Configure your SendGrid API key in Settings first")
    if not lead.email:
        raise HTTPException(status_code=400, detail="Lead has no email address")

    if not (lead.email_subject and lead.email_body):
        provider, ai_key = _ai_key(settings_row, keys)
        if not ai_key:
            raise HTTPException(status_code=400, detail="Configure your AI API key to generate the email")
        try:
            email, tokens = asyncio.run(
                generate_email(
                    {"company_name": lead.company_name, "website": lead.website},
                    lead.audit_data or {},
                    _agency_services(db, user.id),
                    provider=provider,
                    api_key=ai_key,
                )
            )
            lead.email_subject, lead.email_body = email["subject"], email["body"]
            record_ai_usage(db, user.id, tokens)
            db.commit()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Email generation failed: {exc}")

    from config import settings as _cfg

    result = send_email(db, lead, user, settings_row, sendgrid_key, _cfg.APP_BASE_URL)
    if result.get("status") == "sent":
        schedule_followups(db, lead, user.id)
    return result


@router.delete("/leads/{lead_id}", status_code=204)
def delete_lead(
    lead_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead = _get_lead(db, user, lead_id)
    db.query(SequenceStep).filter(SequenceStep.lead_id == lead.id).delete(
        synchronize_session=False
    )
    db.query(EmailLog).filter(EmailLog.lead_id == lead.id).delete(synchronize_session=False)
    db.delete(lead)
    db.commit()
