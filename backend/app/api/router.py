"""All feature routers mounted under the API prefix."""
from __future__ import annotations

from fastapi import APIRouter

from app.analytics.router import router as analytics
from app.asha.router import router as asha
from app.assessment.router import router as assessment
from app.assistant.router import router as assistant
from app.auth.router import router as auth
from app.companion.router import router as companion
from app.emergency.router import router as emergency
from app.emotional.router import router as emotional
from app.gis.router import router as gis
from app.knowledge.router import guide_router
from app.knowledge.router import router as knowledge
from app.messaging.router import router as messaging
from app.notifications.router import router as notifications
from app.passport.router import router as passport
from app.referral.router import router as referral
from app.reports.router import router as reports
from app.users.router import router as users
from app.visits.router import router as visits
from app.voice.router import router as voice

api_router = APIRouter()
for r in (auth, users, passport, asha, assistant, voice, companion, emotional, assessment, reports, knowledge,
          guide_router, gis, referral, emergency, messaging, visits, notifications, analytics):
    api_router.include_router(r)
