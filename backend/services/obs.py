"""Observability (PRD §8): Sentry init + request-id middleware + structured logs."""
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from config import settings


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def setup_observability(app) -> None:
    """Initialise Sentry (if configured) and attach request-id middleware."""
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)
            logging.getLogger("trax9").info("sentry initialised")
        except ImportError:
            logging.getLogger("trax9").warning("sentry-sdk not installed — skipping")
    app.add_middleware(RequestIDMiddleware)
