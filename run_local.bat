@echo off
REM Run Janamdatri-AI on this computer.
REM   App + API:        http://localhost:8000
REM   SMS inbox (OTPs): http://localhost:8025   (Mailpit, started automatically)
REM
REM   run_local.bat            core app (SQLite, no Redis needed)
REM   run_local.bat --with-ai  also installs Whisper (voice), PaddleOCR + Tesseract (report photos) and Coqui TTS
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo Python 3.11 or newer is required. & exit /b 1)
if not exist .env copy .env.example .env >nul
cd backend
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt || exit /b 1
if "%~1"=="--with-ai" (
  REM one package at a time, so a package that doesn't support this Python version doesn't block the others
  for /f "usebackq eol=# tokens=1" %%p in ("requirements-ai.txt") do python -m pip install -q "%%p" || echo   [skipped %%p - not available for this Python or OS]
  python -m scripts.download_models
)
alembic upgrade head || exit /b 1
if not exist models\risk_gbm.joblib python -m scripts.train_risk_model

REM Mailpit: local inbox for every SMS (OTPs, ASHA and family alerts). Opens in its own window.
python -m scripts.mailpit path >nul || exit /b 1
start "Mailpit - SMS inbox" /min cmd /c ".venv\Scripts\python.exe -m scripts.mailpit"

echo.
echo   Janamdatri-AI:        http://localhost:8000   (app: /app.dc.html, API docs: /docs)
echo   SMS inbox (Mailpit):  http://localhost:8025   ^<- OTPs arrive here
echo   Ctrl+C to stop (close the minimised "Mailpit" window to stop the inbox)
echo.
uvicorn app.main:app --host 127.0.0.1 --port 8000
