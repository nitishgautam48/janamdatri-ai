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
├── backend/    FastAPI · SQLAlchemy · Alembic · Gemini / Groq / Azure Speech (+ optional local models) · FAISS
├── share.bat / share.sh   public HTTPS link through a free Cloudflare Tunnel (stop-share.bat to stop)
├── docs/       SHARE_LINK.md (Cloudflare link) · DEPLOY_AZURE.md (optional cloud server)
├── deploy/     share.ps1, Caddyfile, vm-setup.sh, update.sh
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

### Turning on the AI (free cloud keys)

Everything works without keys: answers then come from the built-in 230-topic knowledge base. Add free keys to `.env`
for AI answers, voice and report reading. Each one is optional; the app skips a missing or rate-limited provider and
uses the next.

| Feature | Provider (in order) | Key in `.env` | Where to get it |
|---|---|---|---|
| Health Assistant, Talk To Me, report explanations, daily tip | **Gemini** → **Groq** → OpenRouter → local Ollama → knowledge base | `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` | [aistudio.google.com](https://aistudio.google.com), [console.groq.com](https://console.groq.com), [openrouter.ai/keys](https://openrouter.ai/keys) |
| Smarter knowledge search | Gemini embeddings + keyword search | `GEMINI_API_KEY` | same |
| Voice input (mic buttons) | **Groq Whisper** → Azure Speech → local Whisper | `GROQ_API_KEY`, `AZURE_SPEECH_KEY` | Azure portal → Speech → Free F0 |
| Read aloud | **Azure neural voices** (Hindi / English) → phone or browser voice | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | same |
| Reading report **photos** | **Gemini vision** → OCR.space → Tesseract | `GEMINI_API_KEY`, `OCRSPACE_API_KEY` | [ocr.space/ocrapi](https://ocr.space/ocrapi) |
| Map pictures (served and cached by our server) | **MapTiler** → OpenStreetMap → OSM France → Esri | `MAPTILER_KEY` (optional, free) | cloud.maptiler.com → API keys |
| Road distances and the route line | **openrouteservice** → OSRM → straight-line estimate | `ORS_API_KEY` (optional, free) | openrouteservice.org → Dashboard |
| Extra hospitals and routes (optional) | Azure Maps | `AZURE_MAPS_KEY` | Azure portal → Azure Maps account |

**Privacy:** before any text leaves the server, names, phone numbers, pincodes and IDs are removed (`AI_REDACT=true`)
and her first name is put back into the reply. Set `OCR_CLOUD_ENABLED=false` to keep report photos on the server.
Free tiers of some providers may use requests to improve their models: for real patients, use paid tiers or local models.

**Free limits:** `AI_DAILY_LIMIT`, `STT_DAILY_LIMIT`, `AZURE_STT_MONTHLY_SECONDS` and `AZURE_TTS_MONTHLY_CHARS` keep usage
inside the free tiers; after a limit the next fallback is used. `http://localhost:8000/health` shows every provider
and today's usage.

**Safety:** the care-level engine (WHO / MoHFW rules + the gradient-boosting model) and SOS escalation never call an
AI provider.

**Offline mode (optional):** `docker compose -f docker-compose.yml -f docker-compose.local-ai.yml up -d --build` adds
local models (Ollama, Whisper, PaddleOCR, Coqui TTS; needs ~16 GB RAM) as the backup, or run
`run_local.sh` with `WITH_AI=1` (`run_local.bat --with-ai`).

### Share a public link (free)

Double-click **`share.bat`** (or `./share.sh`): the app gets a public **https://…trycloudflare.com** link through a
free Cloudflare Tunnel, for live demos, the jury or your phone (GPS and microphone work over HTTPS). The SMS inbox
stays private on your computer. Details: [docs/SHARE_LINK.md](docs/SHARE_LINK.md).

For an always-on server instead, see [docs/DEPLOY_AZURE.md](docs/DEPLOY_AZURE.md) (any Ubuntu VM with Docker and
free HTTPS via `docker-compose.prod.yml`).

### Check that everything works

```bash
cd backend && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install pytest && pytest             # 115 tests: engine, OCR, safety, cloud providers (faked), speech accuracy, maps, Plan My Day chat, guide scope, priority engine, every API flow
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
   - **Companion → Plan My Day:** a conversation (type or tap the mic). The companion asks when she wakes and sleeps, her work,
     food, anything special today and how she feels, then builds the day around her answers (meals, tablets, walk, rest).
     She changes it by saying "move walk to 7 pm", "add temple at 6", "remove kegel", or by tapping an item (✏️) or **+ Add**.
     Next day she can say "same as usual". Hydration and medicines from her reports are on the same screen.
   - **Health Assistant:** text, voice and report upload, with danger-sign safety.
   - **Talk To Me:** private emotional companion.
   - **Health check:** gives one of four care levels with what was noticed and the right facility; the result can be shared with her ASHA as a referral.
   - **My ASHA:** chat, photos, location, voice notes, and a masked-call code.
   - **Guide:** the week's milestone plus **Ask about week N**: questions by text or voice, answered for her week. It answers
     only pregnancy, birth, newborn and mother-care questions and politely declines anything else.
   - **Nearby care (map):** the **Nearby Care** card on Home (also ☰ menu → Nearby Care, SOS → To Hospital, or a result's
     *Directions*). Allow location or type a pincode; tap a hospital for the road route.
   - **SOS:** works without login. It alerts her ASHA with her GPS location, finds the nearest 24×7 hospital, and escalates to the backup ASHA, then the ANM, then the facility if no one responds.

**ASHA:** *My area* (share location once: mothers nearby without an ASHA are connected to her) and a map of her mothers coloured by care level, with the road route, Directions and Visit for each.
*Today's work list*: every mother who needs her today, with the reasons (SOS, danger sign told to the app, care level, follow-up, newborn visit day, due date, ANC due, new report, unread message, missed iron tablets, birth preparedness), ordered urgent first and then by the shortest way, with km and time per stop; Remind (app + SMS), Done, Go, and the numbered route on the map.

*Priority engine*: every mother gets a 0–100 score with the reasons, shown as a badge (Critical / High / Medium / Routine) and a "Why first" line on the work list and My Mothers. Emergencies set the score: open SOS 100, a danger sign or self-harm thought told to the app in the last 3 days 95, an Immediate Care result not yet followed up 90. Otherwise points add up from findings (Hb below 7 / 9 / 11, BP 160/110 or 140/90, high sugar), stage (past the due date, first week after birth), risk history (age under 18 or over 35, twins, previous C-section or complications, known conditions) and follow-up gaps, capped at 89. Critical mothers come first by score, the rest by band and then by the shortest way. Rules only, no AI, so every point can be explained.
*Voice visit notes*: she speaks or types ("BP 130 by 85, Hb 9.5, pair mein sujan, bleeding nahi, IFA 30 di, agli visit 3 din baad") and BP, weight, Hb, symptoms, checklist and the next visit are filled in for her to check; the recording is kept as the visit's voice note. *Monthly report* for the ANM (visits, ANC, high-risk, referrals, deliveries, IFA) with estimated incentives (JSY, HBNC): print / PDF, CSV, or SMS summary to the ANM. *Alerts*: 7:30 AM work-list summary, "ASHA visits tomorrow" to mothers at 6:30 PM, and weekly "no news from this mother" alerts.

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
