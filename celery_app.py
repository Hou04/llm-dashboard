"""
Celery application for background jobs.

Broker: Redis database 2 (separate from quota counters and cache)
Backend: Redis database 3 (task result storage)

Workers are started separately from the FastAPI server:
    pipenv run celery -A celery_app worker --loglevel=info
    pipenv run celery -A celery_app beat --loglevel=info

Tasks are in modules/analytics/tasks/
"""

from celery import Celery
from celery.schedules import crontab

from core.settings import settings

# Build Redis URLs for Celery (separate databases from app Redis)
CELERY_BROKER_URL = settings.redis_url.replace("/0", "/2")
CELERY_RESULT_BACKEND = settings.redis_url.replace("/0", "/3")

app = Celery(
    "llm_dashboard",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "modules.analytics.tasks.aggregation",
        "modules.forecasting.tasks.optimization",
        "modules.gateway.tasks",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Scheduled tasks (Celery Beat)
app.conf.beat_schedule = {
    # Nightly aggregation: runs at 2:00 AM UTC every day
    "nightly-cost-aggregation": {
        "task": "modules.analytics.tasks.aggregation.aggregate_yesterday",
        "schedule": crontab(hour=2, minute=0),
    },
    # Monthly rollup: runs at 3:00 AM UTC on the 1st of every month
    "monthly-cost-rollup": {
        "task": "modules.analytics.tasks.aggregation.rollup_last_month",
        "schedule": crontab(hour=3, minute=0, day_of_month=1),
    },
    # Weekly optimization scan: runs at 4:00 AM UTC every Sunday
    "weekly-optimization-scan": {
        "task": "modules.forecasting.tasks.optimization.scan_for_optimizations",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),
    },
}