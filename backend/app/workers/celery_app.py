"""Celery (optional): background report analysis, emergency escalation, reminders and analytics.

Run:  celery -A app.workers.celery_app worker -l info
      celery -A app.workers.celery_app beat -l info
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery = Celery("janamdatri", broker=settings.celery_broker_url, backend=settings.celery_result_backend,
                include=["app.workers.tasks"])
celery.conf.update(
    timezone="Asia/Kolkata", enable_utc=True, task_acks_late=True, worker_prefetch_multiplier=1,
    beat_schedule={
        "escalate-emergencies": {"task": "app.workers.tasks.escalate_emergencies", "schedule": 60.0},
        "anc-reminders": {"task": "app.workers.tasks.anc_reminders", "schedule": crontab(hour=8, minute=0)},
        "hydration-morning": {"task": "app.workers.tasks.wellness_reminders", "schedule": crontab(hour=9, minute=0), "args": ("morning",)},
        "iron-midday": {"task": "app.workers.tasks.wellness_reminders", "schedule": crontab(hour=13, minute=45), "args": ("midday",)},
        "calcium-night": {"task": "app.workers.tasks.wellness_reminders", "schedule": crontab(hour=21, minute=0), "args": ("night",)},
        "asha-morning-worklist": {"task": "app.workers.tasks.asha_alerts", "schedule": crontab(hour=7, minute=30), "args": ("morning",)},
        "visit-tomorrow": {"task": "app.workers.tasks.asha_alerts", "schedule": crontab(hour=18, minute=30), "args": ("evening",)},
        "quiet-mothers": {"task": "app.workers.tasks.asha_alerts", "schedule": crontab(hour=10, minute=0, day_of_week=1), "args": ("weekly",)},
        "analytics-snapshot": {"task": "app.workers.tasks.analytics_snapshot", "schedule": crontab(hour=23, minute=30)},
    },
)
app = celery
