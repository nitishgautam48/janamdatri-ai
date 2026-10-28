@echo off
REM Stop the public link (the app keeps running on http://localhost:8000).
cd /d "%~dp0"
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml stop tunnel
echo Public link stopped. The app still runs at http://localhost:8000
pause
