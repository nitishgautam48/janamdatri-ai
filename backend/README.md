# Janamdatri-AI · Backend

Backend for **Janamdatri-AI (जन्मदात्री)**, a maternal health companion for rural and semi-urban Indian mothers. It provides the API behind every screen in the design (`App Screens`, `Prototype` and `Landing Page v2`):

- Health Passport
- AI Health Assistant
- Pregnancy Companion
- "Talk To Me" emotional companion
- Risk assessment
- OCR report understanding
- GIS navigation to care
- ASHA worker tools
- Secure mother–ASHA messaging
- Referral summaries
- Emergency escalation

> It supports, and never replaces, doctors, ANMs and ASHA workers. Assessment results are care levels, never percentages.

**Stack:** FastAPI · PostgreSQL · SQLAlchemy 2 · Alembic · Redis · JWT · Pydantic v2 · Celery · Docker
**AI:** Gemma 3 Instruct (Ollama or vLLM) · Whisper Large V3 (faster-whisper) · PaddleOCR · FAISS · gradient boosting (scikit-learn) · clinical rules engine

---

## Quick start

From the project root run `./run_local.sh` (Windows: `run_local.bat`) and open http://localhost:8000. OTPs arrive in the Mailpit inbox at http://localhost:8025. See `../README.md` for sign-in details and optional AI models.

Manual setup:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt                # + -r requirements-ai.txt for Whisper + PaddleOCR
alembic upgrade head                               # SQLite file backend/janamdatri.db by default
uvicorn app.main:app --reload                      # also serves ../frontend
python -m scripts.smoke_test http://localhost:8000 # end-to-end journey over HTTP
```

### Tests

```bash
pytest                       # SQLite + in-memory store (47 tests)

# Same suite on PostgreSQL + Redis:
TEST_DATABASE_URL=postgresql+psycopg://…/janamdatri_test TEST_REDIS_URL=redis://localhost:6379/0 pytest
```

The starter reference data (`SEED_REFERENCE_DATA=true`) loads facilities around Bareilly (PHC Rampur, CHC Bhojipura, District Hospital Bareilly…), a pincode/village gazetteer and an ASHA registry (e.g. Sunita Devi `UP-BRL-04417 / SC-2208 / SEC-07`).

---

## AI models: offline by default, real models when configured

| Capability | Default (no GPU, no downloads) | Production setting |
|---|---|---|
| LLM (Gemma 3 Instruct) | If Ollama isn't running, answers come from the curated knowledge base | `ollama` (default, `gemma3:12b`) or `openai` for any OpenAI-compatible server, e.g. vLLM `google/gemma-3-12b-it` |
| Speech-to-text | Voice input off | `WHISPER_PROVIDER=auto` uses faster-whisper `large-v3` when installed |
| OCR | Text layer of PDFs; on-device OCR text via `client_text` | PaddleOCR when installed (`pip install -r requirements-ai.txt`) |
| Retrieval | FAISS over TF-IDF feature hashing (English, Hindi, Hinglish) | `EMBEDDING_PROVIDER=sentence_transformers` (multilingual-e5) |
| Risk model | `HistGradientBoostingClassifier`, trained on a synthetic clinical cohort | Retrain on de-identified data: `python -m scripts.train_risk_model data.csv` |

Gemma 3 comes in 1B, 4B, 12B and 27B sizes; there is no 9B. The default is `gemma3:12b`, and `LLM_MODEL` switches it.

If the model server is down, every AI feature falls back to grounded templates, so the app keeps working.

---

## Architecture

```
app/
├── auth/          OTP (mother, ASHA), guest tokens, family-phone PIN, admin, refresh rotation, revocation
├── users/         profile, preferences, DPDP right to erasure, admin user management
├── passport/      5-step Maternal Health Passport, LMP→EDD, completion, emergency contacts
├── asha/          auto-assignment (village, sub-centre, pincode, sector, GIS), dashboard, register mother
├── visits/        home visit engine (BP, weight, Hb, symptoms, checklist, voice note, next visit)
├── assistant/     Health Assistant: Gemma + FAISS retrieval + safety layer, history, "Tell my ASHA"
├── voice/         Whisper transcription
├── companion/     daily planner, meals, medication reminders from reports, hydration, ANC prep, streaks
├── emotional/     friend-like "Talk To Me": sentiment, pattern memory, mood trend in words, private
├── assessment/    danger ladder, clinical rules, anaemia rules, EPDS, history engine, GBM, worst signal wins
├── reports/       upload → OCR → extraction → validation → explanation, Hb trend, medication schedule
├── ocr/           PaddleOCR / PDF engine, structured extractor, validation rules
├── knowledge/     week-by-week guide (4–40), schemes, nutrition, exercise, wellbeing, danger signs, ANC
├── gis/           geocoding, haversine, nearby facilities, severity routing PHC → CHC → DH
├── referral/      referral summary, PDF, printable HTML, expiring share link, facility SMS
├── emergency/     SOS without login, live location, escalation chain, acknowledgement
├── messaging/     mother ↔ ASHA text, voice, image, report, location, referral; number-masked relay calls
├── notifications/ in-app + SMS, ANC / medication / hydration reminders (idempotent)
├── analytics/     mothers, anaemia trends, ANC compliance, care levels, referrals, emergencies, ASHA activity
├── ai/            LLM client, Whisper, embeddings, FAISS store, safety, sentiment, risk model, prompts
├── database/      models (all tables), session, seed
├── api/           router aggregation, auth/role dependencies, record-level access checks
├── core/          settings, enums, security, cache (Redis), storage, pregnancy maths, rate limits, audit
└── workers/       Celery: report analysis, escalation every minute, reminders, nightly analytics snapshot
```

**Tables:** every table in the spec, plus `emergency_events` and `audit_logs`:

- users, mothers, asha_workers, health_passports, medical_history, pregnancy_profiles
- reports, report_findings, assessments, assessment_results
- conversation_history, emotional_sessions, companion_plans, messages
- emergency_contacts, facilities, gis_mappings, referrals, visits, notifications, analytics

---

## Safety and privacy guarantees (enforced in code and tests)

- **No probabilities.** Assessments return one of *Routine Monitoring / Needs Attention / Medical Review Recommended / Immediate Care Recommended*. The safety layer drops any model sentence that states a percentage, probability or confidence. Tests assert that no `%` appears in any output.
- **The model never decides an emergency.** The gradient-boosting signal can raise the level by at most one step and never above Medical Review. "Immediate care" only comes from explicit clinical danger rules, so the worst rule signal always wins.
- **Danger signs are detected in English, Hindi and Hinglish** in every assistant message. The reply then starts with 108/SOS guidance, and the assigned ASHA is alerted.
- **Self-harm language** triggers a warm crisis response with Tele-MANAS 14416 and 108.
- **"Talk To Me" stays private.** Only the mother can read it. If concern is high, her ASHA gets a check-in nudge without any of the conversation, and the mother is told this happened.
- **Phone numbers are never shown between mother and ASHA.** Calls go through a relay number with a one-time code.
- **Record-level access.** ASHA workers can only reach mothers assigned to them (primary, backup or registered by them). ASHA access to records is audited.
- **Hardening:**
  - OTPs are stored hashed, with expiry, attempt limits and per-number rate limits; they are delivered to the Mailpit inbox.
  - Refresh tokens rotate, and logout revokes tokens.
  - Uploads are type- and magic-byte checked.
  - With `ENVIRONMENT=production` the app refuses to start with a default JWT secret or admin password.

---

## API (107 endpoints, all under `/api/v1`; full schema at `/docs`)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/otp/request`, `/auth/mother/signup`, `/auth/mother/login`, `/auth/guest`, `/auth/pin/set`, `/auth/pin/login`, `/auth/asha/verify`, `/auth/asha/login`, `/auth/admin/login`, `/auth/refresh`, `/auth/logout` |
| Users | `GET/PATCH/DELETE /users/me`, `GET /users`, `PATCH /users/{id}` |
| Passport | `POST /passport/create`, `GET /passport`, `PUT /passport/update`, `GET /passport/completion`, `POST/DELETE /passport/emergency-contacts` |
| ASHA | `GET /asha/assigned`, `POST /asha/link`, `GET/PATCH /asha/me`, `GET /asha/dashboard`, `GET /asha/mothers[?filter=anc_due\|follow_up\|high_risk\|visit_today]`, `GET /asha/mothers/{id}`, `POST /asha/mothers/register` |
| Visits | `POST /visit/create`, `POST /visit/{id}/voice-note`, `GET /visit/history` |
| Assistant | `POST /assistant/chat`, `POST /assistant/voice`, `GET /assistant/history`, `POST /assistant/tell-asha` |
| Voice | `POST /voice/transcribe` |
| Companion | `POST /companion/plan`, `GET /companion/today`, `PATCH /companion/today/tasks/{id}`, `POST /companion/hydration`, `GET /companion/week` |
| Emotional | `POST /emotional/chat`, `GET /emotional/history` |
| Assessment | `GET /assessment/symptoms`, `POST /assessment`, `GET /assessment/history`, `GET /assessment/latest`, `GET /assessment/{id}` |
| Reports | `POST /reports/upload`, `POST /reports/analyze`, `GET /reports/history`, `GET /reports/trends`, `GET /reports/medications`, `GET/DELETE /reports/{id}`, `GET /reports/{id}/file` |
| Knowledge | `GET /guide/week`, `GET /guide/topics`, `POST /knowledge/query` |
| GIS | `GET /gis/nearby`, `GET /gis/facilities`, `GET /gis/facilities/{id}`, `GET /gis/recommendation`, `GET /gis/geocode`; admin: `POST/PUT /gis/facilities`, `POST /gis/mappings` |
| Referral | `POST /referral/generate`, `GET /referral/history`, `GET /referral/{id}`, `GET /referral/{id}/pdf`, `GET /referral/{id}/print`, `GET /referral/shared/{token}`, `PATCH /referral/{id}/status` |
| Emergency | `POST /emergency/alert` (no login), `POST /emergency/location`, `GET /emergency/facilities`, `GET /emergency/numbers`, `GET /emergency/active`, `GET /emergency/{id}`, `POST /emergency/{id}/acknowledge`, `POST /emergency/{id}/resolve`, `POST /emergency/escalate-due` |
| Messaging | `POST /messages/send`, `POST /messages/voice`, `POST /messages/image`, `POST /messages/location`, `GET /messages/history`, `GET /messages/conversations`, `POST /messages/call`, `GET /messages/quick-actions`, `GET /messages/{id}/attachment` |
| Notifications | `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` |
| Analytics | `GET /analytics/overview`, `/anemia-trends`, `/anc-compliance`, `/assessments`, `/referrals`, `/emergencies`, `/asha-activity`, `POST /analytics/snapshot`, `GET /analytics/snapshots` |

Errors use a single shape: `{"error": {"code": "...", "message": "...", "details": {}}}`.

### Design screen → API

| Screen | Endpoint |
|---|---|
| A1–A2 Role selection, mother OTP, guest, ASHA login | `/auth/*` |
| A3–A5 Passport steps 1–5, profile strength | `/passport/*`, `/reports/upload` |
| B1 Home: week, baby size, next ANC, wellness plan, this week's focus | `/companion/today`, `/guide/week` |
| B2 Companion and B2b celebration | `/companion/*` (`celebration` appears when all 5 goals are done) |
| B3 Health Assistant and history | `/assistant/*` |
| B4 Talk To Me | `/emotional/*` |
| B5 Pregnancy Guide | `/guide/week`, `/guide/topics` |
| C1–C2 My ASHA, chat, secure call | `/asha/assigned`, `/messages/*` |
| C3–C4 Health check and result | `/assessment` |
| C5 Report Center, Hb trend, medication schedule | `/reports/*` |
| D1 Nearby Care | `/gis/nearby` |
| D2 Referral Summary | `/referral/*` |
| D3 Emergency Hub (no login) | `/emergency/*` |
| D4 ASHA dashboard | `/asha/dashboard` |
| F1 Register mother | `/asha/mothers/register` |
| F2 Visit notes | `/visit/*` |
| F3 Referral generator | `/referral/generate` |

---

## Assessment engine

Each component emits signals, and the worst signal wins.

| Component | Examples |
|---|---|
| Danger ladder | bleeding, fits, fainting, breathing difficulty, severe belly pain, water leaking → **Immediate**. Reduced fetal movement: Immediate from week 28, Medical Review from week 20. Severe headache plus blurred vision → **Immediate**. |
| Clinical rules | BP ≥160/110 → Immediate. BP ≥140/90 → Medical Review (Immediate with headache or vision symptoms). Fasting sugar ≥92 or OGTT ≥140 → Medical Review. Fever, pulse, urine protein, fetal heart rate, sudden weight gain. |
| Anaemia (WHO) | Hb <7 → Immediate. 7–9.9 → Medical Review. 10–10.9 → Needs Attention. |
| EPDS | Item 10 above 0 → Immediate (with crisis support). Total ≥13 → Medical Review. 10–12 → Needs Attention. |
| History engine | Chronic conditions, past complications, previous C-section, twins, age under 18 or over 35, no IFA, falling Hb trend, missed ANC |
| Pattern model | Gradient boosting: at most one step up, capped at Medical Review |

The four design scenarios (Routine, Hb 10.4, Hb 9.8 with dizziness, BP 160/110 with headache) produce exactly the results shown in the mockups. Facility routing follows severity: Routine → PHC, Medical Review → CHC, Immediate → district hospital.

---

## Prototype notes and what to plug in for production

- **ASHA registry checks are mocked.** `ASHA_MOCK_REGISTRY=true` accepts well-formed new IDs. Connect to the state RCH/ASHA database instead.
- **SMS:** delivered to Mailpit (local inbox, http://localhost:8025) over SMTP; no SMS gateway. `app/notifications/sms.py` is the one place to add a gateway later.
- **Masked calls.** The relay returns a virtual number and a code. Bridge them with a telephony provider such as Exotel or Knowlarity.
- **Uploads are stored locally** under `MEDIA_ROOT` (a Docker volume). Swap `app/core/storage.py` for S3/GCS with encryption at rest.
- **Clinical review.** Clinical thresholds, knowledge content and prompts should be reviewed by your medical team before going live.
- **Referral PDFs** are English-only. Bundle a Devanagari font for Hindi PDFs.
