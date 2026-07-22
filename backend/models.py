"""ORM models — all tables from PRD §1.

Type notes for SQLite-dev / Postgres-prod portability:
- UUIDs use sqlalchemy.Uuid (native uuid on PG, CHAR(32) on SQLite).
- JSONB / TEXT[] columns use JSON (JSONB on PG via variant, TEXT on SQLite).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# JSONB on Postgres, plain JSON elsewhere
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.utcnow()


# --------------------------------------------------------------------------- enums

class CampaignStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    completed = "completed"
    paused = "paused"


class LeadStatus(str, enum.Enum):
    discovered = "discovered"
    finding_email = "finding_email"
    auditing = "auditing"
    audited = "audited"
    scored = "scored"
    enriching = "enriching"
    enriched = "enriched"
    writing = "writing"
    written = "written"
    queued = "queued"
    sending = "sending"
    sent = "sent"
    opened = "opened"
    replied = "replied"
    bounced = "bounced"
    unsubscribed = "unsubscribed"
    failed = "failed"


class EmailSource(str, enum.Enum):
    scraped = "scraped"
    pattern = "pattern"
    hunter = "hunter"
    manual = "manual"


class EmailLogStatus(str, enum.Enum):
    queued = "queued"
    sent = "sent"
    bounced = "bounced"
    opened = "opened"
    clicked = "clicked"
    replied = "replied"
    dropped = "dropped"
    spam = "spam"


class SequenceStepStatus(str, enum.Enum):
    scheduled = "scheduled"
    sent = "sent"
    skipped = "skipped"
    cancelled = "cancelled"


class SuppressionReason(str, enum.Enum):
    unsubscribe = "unsubscribe"
    spam = "spam"
    bounce = "bounce"
    manual = "manual"


class TaskType(str, enum.Enum):
    profile = "profile"
    discovery = "discovery"
    email_find = "email_find"
    audit = "audit"
    score = "score"
    write = "write"
    send = "send"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class AIProvider(str, enum.Enum):
    openai = "openai"
    anthropic = "anthropic"


class UserPlan(str, enum.Enum):
    free = "free"
    pro = "pro"


# --------------------------------------------------------------------------- tables

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    plan: Mapped[UserPlan] = mapped_column(Enum(UserPlan), default=UserPlan.free)
    monthly_email_quota: Mapped[int] = mapped_column(Integer, default=1000)
    # Bumped to invalidate all outstanding JWTs (logout-everywhere / password change).
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Email verification (audit 1.5) — sending is blocked until verified.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    settings: Mapped["UserSettings"] = relationship(back_populates="user", uselist=False)


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    # Fernet-encrypted JSON blob of all third-party API keys
    encrypted_keys: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    physical_address: Mapped[str | None] = mapped_column(Text, nullable=True)  # CAN-SPAM footer
    max_emails_per_hour: Mapped[int] = mapped_column(Integer, default=50)
    max_emails_per_day: Mapped[int] = mapped_column(Integer, default=100)
    send_start_hour: Mapped[int] = mapped_column(Integer, default=8)
    send_end_hour: Mapped[int] = mapped_column(Integer, default=18)
    warmup_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    warmup_daily_cap: Mapped[int] = mapped_column(Integer, default=10)
    ai_provider: Mapped[AIProvider] = mapped_column(Enum(AIProvider), default=AIProvider.anthropic)
    social_enrich_min_score: Mapped[int] = mapped_column(Integer, default=60)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_user: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="settings")


class AgencyProfile(Base):
    __tablename__ = "agency_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    website: Mapped[str] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    services: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)       # [{name, description}]
    ideal_client: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)   # {industries[], ...}
    suggested_keywords: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    suggested_locations: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    positioning: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_analysis: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    agency_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agency_profiles.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    seed_keywords: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    target_locations: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    industry_filters: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus), default=CampaignStatus.draft)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("campaign_id", "website", name="uq_lead_campaign_website"),
        Index("ix_leads_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    website: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_source: Mapped[EmailSource | None] = mapped_column(Enum(EmailSource), nullable=True)
    email_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus), default=LeadStatus.discovered)
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    score_reasons: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    audit_data: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    email_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AuditCache(Base):
    __tablename__ = "audit_cache"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    audit_data: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sg_event_ids: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)  # webhook dedup
    from_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    to_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_step: Mapped[int] = mapped_column(Integer, default=0)  # 0 = first touch
    status: Mapped[EmailLogStatus] = mapped_column(Enum(EmailLogStatus), default=EmailLogStatus.queued)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bounce_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SequenceStep(Base):
    __tablename__ = "sequence_steps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    step_number: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SequenceStepStatus] = mapped_column(
        Enum(SequenceStepStatus), default=SequenceStepStatus.scheduled
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Suppression(Base):
    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_suppression_user_email"),
        Index("ix_suppressions_user_email", "user_id", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(String(320))
    reason: Mapped[SuppressionReason] = mapped_column(Enum(SuppressionReason))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True, index=True
    )
    type: Mapped[TaskType] = mapped_column(Enum(TaskType))
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.pending)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("user_id", "period", name="uq_usage_user_period"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    emails_sent: Mapped[int] = mapped_column(Integer, default=0)
    ai_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    places_calls: Mapped[int] = mapped_column(Integer, default=0)
    socialcrawl_credits: Mapped[int] = mapped_column(Integer, default=0)
