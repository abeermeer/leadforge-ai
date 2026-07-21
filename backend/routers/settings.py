"""GET/PUT /settings — sending config + Fernet-encrypted API keys (masked on read)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from crypto import decrypt_dict, encrypt_dict, mask_key
from deps import get_db, get_user_settings
from models import UserSettings
from schemas import SettingsIn, SettingsOut

router = APIRouter()


def _to_out(row: UserSettings) -> SettingsOut:
    keys = decrypt_dict(row.encrypted_keys)
    return SettingsOut(
        keys_masked={name: mask_key(value) for name, value in keys.items() if value},
        from_email=row.from_email,
        from_name=row.from_name,
        physical_address=row.physical_address,
        max_emails_per_hour=row.max_emails_per_hour,
        max_emails_per_day=row.max_emails_per_day,
        send_start_hour=row.send_start_hour,
        send_end_hour=row.send_end_hour,
        warmup_enabled=row.warmup_enabled,
        warmup_daily_cap=row.warmup_daily_cap,
        ai_provider=row.ai_provider.value if hasattr(row.ai_provider, "value") else row.ai_provider,
        social_enrich_min_score=row.social_enrich_min_score,
        imap_host=row.imap_host,
        imap_user=row.imap_user,
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings_endpoint(row: UserSettings = Depends(get_user_settings)):
    return _to_out(row)


@router.put("/settings", response_model=SettingsOut)
def update_settings(
    body: SettingsIn,
    row: UserSettings = Depends(get_user_settings),
    db: Session = Depends(get_db),
):
    # Merge API keys: only overwrite keys the client actually sent
    if body.keys is not None:
        current = decrypt_dict(row.encrypted_keys)
        incoming = body.keys.model_dump(exclude_none=True)
        current.update(incoming)
        row.encrypted_keys = encrypt_dict(current)

    for field in (
        "from_email", "from_name", "physical_address",
        "max_emails_per_hour", "max_emails_per_day",
        "send_start_hour", "send_end_hour",
        "warmup_enabled", "ai_provider", "social_enrich_min_score",
        "imap_host", "imap_user",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)

    db.commit()
    db.refresh(row)
    return _to_out(row)
