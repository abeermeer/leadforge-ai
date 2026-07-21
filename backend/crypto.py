"""Fernet encryption for per-user secrets (user_settings.encrypted_keys).

Keys are stored as an encrypted JSON blob and decrypted only at the moment of use.
The Settings API must never return a full key — use mask_key() for display.
"""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from config import settings


def _fernet() -> Fernet:
    key = settings.FERNET_KEY
    if not key:
        # Dev-only fallback: derive a stable key from SECRET_KEY.
        # Production MUST set FERNET_KEY explicitly.
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
        return {}


def mask_key(value: str) -> str:
    """'sk-abcdefghijklmnop' -> 'sk-a...mnop'. Short values fully masked."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
