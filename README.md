# Janamdatri-AI · जन्मदात्री

A maternal health companion for rural and semi-urban Indian mothers. It runs entirely on your own computer and connects mothers, ASHA workers and health facilities:

- Health Passport
- AI Health Assistant
- Pregnancy and Emotional Companions
- Care-level assessment (never percentages)
- Report OCR
- GIS navigation to care
- Secure mother–ASHA messaging
- Referrals
- Emergency escalation

```
janamdatri-ai/
├── run_local.sh / run_local.bat   one command to set up and start everything (app + Mailpit SMS inbox)
├── frontend/   your design files, wired to the API
│   ├── index.html        Landing page            (Landing Page v2.dc.html)
│   ├── app.dc.html       Mother & ASHA web app    (Prototype.dc.html + login/passport screens from App Screens)
│   ├── screens.dc.html   All screens, design reference (App Screens.dc.html)
│   ├── janamdatri-api.js API client (auth, token refresh, uploads, GPS, voice recording)
│   ├── janamdatri-ui.js  हिंदी / English switch and light / dark theme
│   ├── janamdatri-i18n.js Hindi wording for strings that are English-only in the design
│   ├── support.js, image-slot.js, illustrations/   from the design package
│   └── vendor/           React 18 and the Lucide icon font, bundled (no CDN needed)
├── backend/    FastAPI · SQLAlchemy · Alembic · Gemma · Whisper · PaddleOCR · FAISS (see backend/README.md)
└── .env.example            local settings (copied to .env on first run)
```

## Run it locally

You need **Python 3.11, 3.12 or 3.13** and an internet connection on the first run. Nothing else is required: the database is a local SQLite file, no Redis is needed, and [Mailpit](https://mailpit.axllent.org) (the local SMS inbox) is downloaded and started automatically.

**macOS / Linux**

```bash
cd janamdatri-ai
./run_local.sh
```

**Windows**

```bat
cd janamdatri-ai
run_local.bat
```

Then open:

- **http://localhost:8000**: landing page (the app is at `/app.dc.html`, the API reference at `/docs`)
- **http://localhost:8025**: the **SMS inbox** (Mailpit), where OTPs and alerts arrive

The first run creates a virtual environment, installs the packages, creates the database (`backend/janamdatri.db`) and loads the starter data. Later runs start in a few seconds. Stop with Ctrl+C.

### Signing in: OTPs arrive in Mailpit

No SMS gateway is used. Every text message the system sends lands in the local Mailpit inbox at **http://localhost:8025**, addressed to the phone number (e.g. `919876544210@sms.janamdatri.local`):

- sign-in OTPs for mothers and ASHA workers
- emergency alerts to ASHA workers and family contacts
- referral notices to facilities

After *Send OTP*, tap **Open SMS inbox (Mailpit)** in the app, read the 6-digit code and type it in. Nothing leaves your computer.

Login and Sign Up always start with **Who are you?** (role selection): choose *I Am A Mother* or *I Am An ASHA Worker*, then Login or Sign Up.

- **Mother (new):** Sign Up → *I Am A Mother* → any 10-digit mobile number starting with 6–9 → Send OTP → read the OTP in Mailpit → Verify → create the **Health Passport in 5 steps**:
  1. About You
  2. Pregnancy (LMP → week and due date)
  3. Medical History
  4. Upload Reports (photo or PDF, read automatically)
  5. Emergency Contacts & preferred facility

  Use village **Rampur** and pincode **243001** to be matched with the starter ASHA workers and facilities. Steps 3–5 can be skipped and finished later from *Profile*.
- **Mother (returning):** Login → *I Am A Mother* → mobile number → OTP. On a shared family phone, set a 4-digit PIN in *Profile → Shared phone mode*, then choose *Login with your 4-digit PIN*.
- **ASHA worker:** Login → *I Am An ASHA Worker* → `UP-BRL-04417`, `SC-2208`, `SEC-07` (Sunita Devi, Rampur Sub Centre) → Verify ID → OTP from Mailpit (sent to `919800044421`) → Continue. Others in the starter registry: `UP-BRL-04418` (Kamla Yadav, `SC-2208`/`SEC-07`), `UP-BRL-04502` (Geeta Kumari, `SC-2215`/`SEC-08`). A new, well-formed ID (e.g. `UP-BRL-05001`, `SC-2208`, `SEC-07`) is registered after adding name and mobile.
- A mother's number entered on the ASHA screen, or an ASHA worker's number on the mother screen, is redirected to the right login.
- **Guest:** *Continue as Guest* gives the pregnancy guide, health questions, nearby care and SOS.
- **Admin API:** `admin@janamdatri.local` / `ChangeMe!2026`, via `POST /api/v1/auth/admin/login` (change it in `.env`).

### Turning on the AI models (optional)

Everything works without them. When they are available they are used automatically:

| Feature | Uses | Without it | How to turn it on |
|---|---|---|---|
| Health Assistant, Talk To Me, report explanations, daily tip | **Gemma 3 Instruct** | Answers from the built-in, curated knowledge base | Install [Ollama](https://ollama.com), then `ollama pull gemma3:12b` (or `gemma3:4b` on a laptop without a GPU, and set `LLM_MODEL=gemma3:4b` in `.env`) |
| Voice input (mic buttons) | **Whisper Large V3** | Voice is off; typing works | `WITH_AI=1 ./run_local.sh` (Windows: `run_local.bat --with-ai`). Downloads the models once. |
| Reading report **photos** | **PaddleOCR** | PDFs with text are still read | Same as above |

`http://localhost:8000/health` shows what is active.

### Other options

- **PostgreSQL / Redis:** set `DATABASE_URL` / `REDIS_URL` in `.env`.
- **Mailpit already installed** (Homebrew, Scoop, Docker)? It's used as is if it's on your PATH or already listening on port 1025.
- **Docker:** `docker compose up -d --build` runs the same app with PostgreSQL, Redis, Celery workers, Ollama and Mailpit.
- **Use from a phone on the same Wi-Fi:** start with `uvicorn app.main:app --host 0.0.0.0` (from `backend/` with the venv active) and open `http://<your-computer's-IP>:8000`. GPS and microphone need HTTPS or localhost in most browsers.

### Check that everything works

```bash
cd backend && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install pytest && pytest             # 49 tests: engine, OCR, safety, Mailpit delivery, every API flow
python -m scripts.smoke_test http://localhost:8000   # full journey with the server running; reads OTPs from Mailpit
```

---

## Using the app

**Language and theme:** the हिंदी / English switch and the light / dark (☀/☾) button are on the first screen and in *Profile*. The choice is remembered on the device, and the Health Assistant and Talk To Me reply in the chosen language. The landing page has the same language switch (bottom left).

**Mother**

1. Sign up with OTP.
2. Fill the Health Passport. Week and EDD are calculated, and an ASHA worker is assigned automatically by village, sub-centre, pincode and distance.
3. Use the daily screens:
   - **Home:** week, baby size, next ANC, today's wellness goals.
   - **Companion:** Plan My Day, hydration, medicines from her reports.
   - **Health Assistant:** text, voice and report upload, with danger-sign safety.
   - **Talk To Me:** private emotional companion.
   - **Health check:** gives one of four care levels with what was noticed and the right facility; the result can be shared with her ASHA as a referral.
   - **My ASHA:** chat, photos, location, voice notes, and a masked-call code.
   - **Guide** and **Nearby care**.
   - **SOS:** works without login. It alerts her ASHA with her GPS location, finds the nearest 24×7 hospital, and escalates to the backup ASHA, then the ANM, then the facility if no one responds.

**Mother, more screens:** *Profile* (passport strength, language, theme, read-aloud, emergency contacts, preferred facility, shared-phone PIN), *My Reports* (take a photo or upload a PDF, hemoglobin trend, extracted values, medicines), *Referral slip* for the doctor after a health check (download PDF, share link, print, Show Doctor), and *Chat history* in the Health Assistant.

**ASHA worker:** the dashboard shows assigned mothers, today's visits, ANC due and live emergency cases. Tap *Open* to respond, and the mother sees "ASHA is responding". From the dashboard:
- **Register Mother:** name, age, mobile, village, RCH ID → fill her 5-step passport on her behalf. She can later sign in with that number.
- **My Mothers:** search and filter (ANC due, follow-up, high risk, visit today), then per mother:
  - **Home Visit:** BP, weight, Hb, checklist, notes or a voice note → the care level is checked and the next visit is scheduled.
  - **Referral:** care level → auto-matched PHC / CHC / district hospital → reason → sent to the mother, and the facility is notified.
  - **Passport:** view or edit it.

> Text messages (OTPs, alerts to ASHA workers, family and facilities) go to the Mailpit inbox; in-app notifications work as normal. Adding a real SMS gateway later means changing a single function, `send_sms` in `backend/app/notifications/sms.py`.

---

## How the design files were wired

Your HTML files from the design package are the frontend. Their layout and styles are unchanged:

- Static sample values ("Priya", "Week 24", "Fri, 12 Oct · PHC Rampur", "118/76", "Sunita Devi"…) became `{{ bindings }}` filled from the API.
- The "Type or speak…" bars became real inputs, and the buttons call the API.
- The prototype's script was replaced with live logic that talks to the backend.
- These screens were ported from `App Screens.dc.html`:
  - role selection with Login / Sign Up (A1)
  - mother login (A1b) and guest hub (A1c)
  - ASHA login (A2)
  - the 5-step passport and its completion card (A3–A5)
  - Profile (B7)
  - Chat history (B3b)
  - My Reports (C5)
  - Referral slip (D2)
  - the ASHA tools Register Mother, Home Visit and Referral Generator (F1–F3)
- The light theme uses the colours of the design's light variant (E1–E11).
- The designer-only side panel and the fake phone status bar were removed. On phones the app is full-screen; on desktop it is shown at phone width.
- On the landing page, Login, Sign Up, Create Health Passport, Emergency, Talk To Me, Message ASHA and Nearest hospital open the app. Call 108 dials 108.

Before real-world use, have the clinical thresholds (`backend/app/assessment/engine.py`), the health content (`backend/app/knowledge/`) and the AI prompts (`backend/app/ai/prompts.py`) reviewed by a medical team. Also replace the Bareilly starter data (`backend/app/database/seed.py`) with your district's facilities, villages and ASHA registry.
