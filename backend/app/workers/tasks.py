from __future__ import annotations

import uuid

from app.database.models import Report
from app.database.session import SessionLocal
from app.workers.celery_app import celery


@celery.task(bind=True, max_retries=2, default_retry_delay=30)
def analyze_report_task(self, report_id: str, client_text: str | None = None, language: str = "en"):
    from app.reports.service import analyze_report

    with SessionLocal() as db:
        r = db.get(Report, uuid.UUID(report_id))
        if not r:
            return "missing"
        try:
            analyze_report(db, r, client_text, language)
            db.commit()
        except Exception as exc:
            db.rollback()
            raise self.retry(exc=exc) from exc
        return r.status


@celery.task
def escalate_emergencies():
    from app.emergency.service import escalate_due

    with SessionLocal() as db:
        return escalate_due(db)


@celery.task
def anc_reminders():
    from app.notifications.reminders import anc_reminders as run

    with SessionLocal() as db:
        n = run(db)
        db.commit()
        return n


@celery.task
def wellness_reminders(slot: str):
    from app.notifications.reminders import daily_wellness_reminders

    with SessionLocal() as db:
        n = daily_wellness_reminders(db, slot=slot)
        db.commit()
        return n


@celery.task
def analytics_snapshot():
    from app.analytics.service import take_snapshot

    with SessionLocal() as db:
        return str(take_snapshot(db).snapshot_date)
