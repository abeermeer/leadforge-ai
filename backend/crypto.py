"""Fernet encryption for per-user secrets (user_settings.encrypted_keys).

Keys are stored as an encrypted JSON blob and decrypted only at the moment of use.
The Settings API must never return a full key — use mask_key() for display.
"""
import base64
import hashlib
import json
import logging

from cryptography.fernet import Fernet, InvalidToken

from config import settings

logger = logging.getLogger("trax9.crypto")


def _fernet() -> Fernet:
    key = settings.FERNET_KEY
    if not key:
        # Dev-only fallback: derive a stable key from SECRET_KEY. This path is
        # forbidden outside DEBUG — main.py's lifespan refuses to boot a
        # non-DEBUG app with an empty FERNET_KEY, so this only runs in local dev.
        if not settings.DEBUG:
            raise RuntimeError(
                "FERNET_KEY is not set and DEBUG is False — refusing to derive a "
                "key from SECRET_KEY in production. Set FERNET_KEY."
            )
        logger.warning(
            "FERNET_KEY not set — deriving a DEV-ONLY key from SECRET_KEY. "
            "Never do this in production."
        )
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_dict(data: dict) -> bytes:
    """Encrypt a dict of secrets -> opaque bytes for storage."""
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt_dict(token: bytes | None) -> dict:
    """Decrypt stored bytes -> dict. Empty/invalid -> {} (never raises to caller)."""
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(bytes(token)))
    except (InvalidToken, ValueError):
        # Corrupted blob, or (more likely) FERNET_KEY was rotated without
        # re-encrypting existing rows. Log loudly — silently returning {} looks
        # identical to "user configured no keys" and hides a rotation mistake.
        logger.warning(
            "decrypt_dict: could not decrypt a secrets blob (corrupt data or "
            "FERNET_KEY mismatch after rotation) — treating as empty."
        )
        return {}


def mask_key(value: str) -> str:
    """'sk-abcdefghijklmnop' -> 'sk-a...mnop'. Short values fully masked."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
