"""Pydantic request/response schemas (v2 style)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# --------------------------------------------------------------------------- auth

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    name: str
    plan: str
    monthly_email_quota: int
    email_verified: bool = False
    created_at: datetime


# --------------------------------------------------------------------------- settings

class ApiKeysIn(BaseModel):
    """Any subset may be sent; only provided keys are updated. None = leave as-is."""
    openai: str | None = None
    anthropic: str | None = None
    sendgrid: str | None = None
    google_places: str | None = None
    google_custom_search: str | None = None
    google_custom_search_cx: str | None = None
    google_pagespeed: str | None = None
    hunter: str | None = None
    socialcrawl: str | None = None
    imap_password: str | None = None


class SettingsIn(BaseModel):
    keys: ApiKeysIn | None = None
    from_email: EmailStr | None = None
    from_name: str | None = None
    physical_address: str | None = None
    max_emails_per_hour: int | None = Field(default=None, ge=1, le=1000)
    max_emails_per_day: int | None = Field(default=None, ge=1, le=10000)
    send_start_hour: int | None = Field(default=None, ge=0, le=23)
    send_end_hour: int | None = Field(default=None, ge=0, le=23)
    warmup_enabled: bool | None = None
    ai_provider: str | None = Field(default=None, pattern="^(openai|anthropic)$")
    social_enrich_min_score: int | None = Field(default=None, ge=0, le=100)
    imap_host: str | None = None
    imap_user: str | None = None


class SettingsOut(BaseModel):
    """Keys are MASKED for display — full values never leave the server."""
    keys_masked: dict[str, str]
    from_email: str | None
    from_name: str | None
    physical_address: str | None
    max_emails_per_hour: int
    max_emails_per_day: int
    send_start_hour: int
    send_end_hour: int
    warmup_enabled: bool
    warmup_daily_cap: int
    ai_provider: str
    social_enrich_min_score: int
    imap_host: str | None
    imap_user: str | None


# --------------------------------------------------------------------------- campaigns

class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    seed_keywords: list[str] = []
    target_locations: list[str] = []
    industry_filters: list[str] = []
    agency_profile_id: uuid.UUID | None = None


class CampaignUpdate(BaseModel):
    name: str | None = None
    seed_keywords: list[str] | None = None
    target_locations: list[str] | None = None
    industry_filters: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(draft|running|completed|paused)$")


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    seed_keywords: list | None
    target_locations: list | None
    industry_filters: list | None
    status: str
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- leads

class LeadUpdate(BaseModel):
    email: EmailStr | None = None
    email_subject: str | None = None
    email_body: str | None = None
    company_name: str | None = None


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    company_name: str
    website: str
    phone: str | None
    email: str | None
    email_source: str | None
    email_confidence: int | None
    city: str | None
    country: str | None
    category: str | None
    source: str | None
    status: str
    fit_score: int | None
    email_subject: str | None
    sent_at: datetime | None
    opened_at: datetime | None
    replied_at: datetime | None
    created_at: datetime


class LeadDetailOut(LeadOut):
    address: str | None
    score_reasons: list | None
    audit_data: dict | None
    email_body: str | None


# --------------------------------------------------------------------------- profile

class ProfileAnalyzeRequest(BaseModel):
    website: str = Field(min_length=4)


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    website: str
    company_name: str | None
    services: list | None
    ideal_client: dict | None
    suggested_keywords: list | None
    suggested_locations: list | None
    positioning: str | None
    updated_at: datetime


# --------------------------------------------------------------------------- tasks / misc

class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class Paginated(BaseModel):
    total: int
    page: int
    page_size: int
    items: list


class LeadSubset(BaseModel):
    """Optional list of lead ids to target an action at a selected subset."""
    lead_ids: list[uuid.UUID] | None = None
