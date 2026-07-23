"""Email normalization + one-way hashing for suppression records.

Two jobs:

1. `normalize_email` — the single definition of "the same address". Suppression
   lookups MUST be case-insensitive: mail providers treat local-parts
   case-insensitively in practice, so an exact-case match lets a re-discovered
   `Owner@Acme.com` slip past a suppression stored as `owner@acme.com` and
   re-mail someone who opted out.

2. `hash_email` — lets a privacy purge erase the plaintext address while
   keeping the do-not-contact record. Without this, honouring "delete my data"
   would delete the very record that stops us contacting them again.

The salt is derived from SECRET_KEY so hashes are stable per deployment and not
reversible via a public rainbow table.
"""
import hashlib

from config import settings


def normalize_email(email: str | None) -> str:
    """Canonical form used for every suppression comparison."""
    return (email or "").strip().lower()


def hash_email(email: str | None) -> str:
    """Salted SHA-256 of the normalized address. Empty input -> empty string."""
    norm = normalize_email(email)
    if not norm:
        return ""
    return hashlib.sha256(f"{settings.SECRET_KEY}:{norm}".encode("utf-8")).hexdigest()
