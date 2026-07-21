"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from config import settings
from database import Base, engine
from routers import auth as auth_router
from routers import campaigns as campaigns_router
from routers import leads as leads_router
from routers import profile as profile_router
from routers import settings as settings_router
from routers import webhooks as webhooks_router
from services.obs import setup_observability

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("trax9")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables on boot when running SQLite without Alembic.
    # Postgres deployments run `alembic upgrade head` before start (see compose).
    if settings.DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    logger.info("startup complete db=%s", settings.DATABASE_URL.split("@")[-1])
    yield
    logger.info("shutdown")


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak stack traces to clients
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health():
    """DB (+ Redis when configured) liveness for Docker/deploy healthchecks."""
    status: dict[str, str] = {"app": "ok"}
    code = 200

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["db"] = "ok"
    except Exception:
        status["db"] = "down"
        code = 503

    try:
        import redis  # optional in local dev

        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        status["redis"] = "ok"
    except ModuleNotFoundError:
        status["redis"] = "not installed"
    except Exception:
        status["redis"] = "down"
        # Redis is required for Celery + rate limits, but the API itself can serve
        # reads without it; report degraded rather than hard-fail.
        status["degraded"] = "true"

    return JSONResponse(status_code=code, content=status)


app.include_router(auth_router.router, prefix="/api", tags=["auth"])
app.include_router(settings_router.router, prefix="/api", tags=["settings"])
app.include_router(profile_router.router, prefix="/api", tags=["profile"])
app.include_router(campaigns_router.router, prefix="/api", tags=["campaigns"])
app.include_router(leads_router.router, prefix="/api", tags=["leads"])
app.include_router(webhooks_router.router, prefix="/api", tags=["webhooks"])

setup_observability(app)
