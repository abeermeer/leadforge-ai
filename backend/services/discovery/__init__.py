"""Lead discovery services: Google Maps/Places, Google Custom Search, directories.

Shared helpers used by every discovery source live here — most importantly
normalize_domain(), the canonical domain form all sources dedupe on (PRD §3A).
"""
from urllib.parse import urlparse


def normalize_domain(url: str) -> str:
    """Normalize a URL or bare host to a canonical domain for dedup.

    'https://www.Foo.com:8080/bar/' -> 'foo.com'. Handles missing scheme,
    lowercases, strips www., port, userinfo and trailing slashes.
    Returns '' for empty or unparseable input.
    """
    if not url:
        return ""
    candidate = url.strip().rstrip("/")
    if not candidate:
        return ""
    if "://" not in candidate and not candidate.startswith("//"):
        candidate = "//" + candidate
    try:
        host = urlparse(candidate).netloc
    except ValueError:
        return ""
    if not host:
        return ""
    host = host.lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.strip(".")
