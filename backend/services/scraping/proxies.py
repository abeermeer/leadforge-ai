"""Proxy pool + user-agent rotation for anti-ban scraping (PRD section 7).

The proxy pool comes from settings.PROXY_POOL (comma-separated URLs, e.g.
"http://user:pass@host1:8080,http://user:pass@host2:8080"). An empty pool
means direct connections. User agents rotate over a small set of realistic
current Chrome/Firefox/Safari strings.
"""
import random
import threading

from config import settings

_rr_lock = threading.Lock()
_rr_index = 0

_USER_AGENTS: list[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0",
    # Firefox on Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Safari/605.1.15",
    # Safari on older macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]


def _pool() -> list[str]:
    """Parse settings.PROXY_POOL into a list of proxy URLs (may be empty)."""
    return [p.strip() for p in settings.PROXY_POOL.split(",") if p.strip()]


def get_proxy() -> str | None:
    """Return the next proxy URL round-robin, or None when the pool is empty."""
    global _rr_index
    pool = _pool()
    if not pool:
        return None
    with _rr_lock:
        proxy = pool[_rr_index % len(pool)]
        _rr_index += 1
    return proxy


def get_user_agent() -> str:
    """Return a random realistic browser user-agent string."""
    return random.choice(_USER_AGENTS)
