"""Celery app + queue config. Task modules register in Phase 2+."""
import os

from celery import Celery
from kombu import Exchange, Queue

celery_app = Celery(
    "trax9_tasks",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)

QUEUES = ("profile", "discovery", "email_find", "audit", "email")

celery_app.conf.task_queues = [
    Queue(q, Exchange(q), routing_key=f"{q}.#") for q in QUEUES
]

celery_app.conf.task_routes = {
    "tasks.profile_tasks.*": {"queue": "profile"},
    "tasks.discovery_tasks.*": {"queue": "discovery"},
    "tasks.audit_tasks.*": {"queue": "audit"},
    "tasks.send_tasks.*": {"queue": "email"},
    "tasks.sequence_tasks.*": {"queue": "email"},
}

celery_app.conf.worker_concurrency = 5
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.task_soft_time_limit = 300
celery_app.conf.task_time_limit = 600

# Register all task modules so workers + beat discover them.
celery_app.autodiscover_tasks(
    [
        "tasks.discovery_tasks",
        "tasks.audit_tasks",
        "tasks.send_tasks",
        "tasks.profile_tasks",
        "tasks.sequence_tasks",
        "tasks.ops_tasks",
    ]
)

celery_app.conf.task_routes["tasks.ops_tasks.*"] = {"queue": "email"}

# Beat schedule is populated by tasks.sequence_tasks and tasks.ops_tasks at
# import time — both must .update() it, never reassign, or one clobbers the other.
celery_app.conf.beat_schedule = {}
