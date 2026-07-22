"""Redis-backed fixed-window rate limiter (audit 1.4).

Fails OPEN when Redis is unreachable — availability over strictness in dev, and
the app already treats Redis as optional. In production Redis is present, so the
limits are enforced. Used by auth endpoints, profile-analyze, and the public
webhooks.
"""
import logging

from config import settings

logger = logging.getLogger("trax9.ratelimit")

_redis = None
_redis_tried = False


def _client():
    global _redis, _redis_tried
    if _redis_tried:
        return _redis
    _redis_tried = True
    try:
        import redis

        _redis = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        _redis.ping()
    except Exception as exc:
        logger.warning("rate limiter: Redis unavailable (%s) — failing open", exc.__class__.__name__)
        _redis = None
    return _redis


def check(key: str, max_hits: int, window_sec: int) -> bool:
    """Increment the counter for `key`. Return True if still within the limit.

    Fixed window: first hit sets the TTL. Fails OPEN (returns True) if Redis is
    down or errors.
    """
    client = _client()
    if client is None:
        return True
    try:
        full_key = f"rl:{key}"
        hits = client.incr(full_key)
        if hits == 1:
            client.expire(full_key, window_sec)
        return int(hits) <= max_hits
    except Exception as exc:
        logger.warning("rate limiter: check failed (%s) — allowing", exc.__class__.__name__)
        return True


def client_ip(request) -> str:
    """Best-effort client IP for keying (honors a single proxy's X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
