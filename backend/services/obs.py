"""Observability (PRD §8 / audit §7): Sentry, request-id middleware + log correlation."""
import contextvars
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from config import settings

# Current request id, readable from any log record on the handling thread/task.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class _RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


def setup_observability(app) -> None:
    """Initialise Sentry (captures unhandled + logged errors), attach request-id
    middleware, and thread the request id into every log line (audit §7)."""
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration

            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                traces_sample_rate=0.1,
                # Capture ERROR-level logs (incl. the global handler) as events.
                integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
            )
            logging.getLogger("trax9").info("sentry initialised")
        except ImportError:
            logging.getLogger("trax9").warning("sentry-sdk not installed — skipping")

    # Add [request_id] to the root formatter so every line inside a request is correlated.
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(_RequestIDFilter())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s")
        )
    app.add_middleware(RequestIDMiddleware)
