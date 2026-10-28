# Start Janamdatri-AI with a free public HTTPS link (Cloudflare quick tunnel) and print the link.
# Run with share.bat (double-click), or: powershell -ExecutionPolicy Bypass -File deploy\share.ps1
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$files = @("-f", "docker-compose.yml", "-f", "docker-compose.tunnel.yml")

Write-Host "Starting Janamdatri-AI and the public link..." -ForegroundColor Cyan
docker compose @files up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not start. Open Docker Desktop, wait for 'Engine running', then try again." -ForegroundColor Red
    exit 1
}

Write-Host "Waiting for the link (up to 90 seconds)..."
$url = $null
for ($i = 0; $i -lt 45 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    $log = (docker compose @files logs tunnel 2>&1 | Out-String)
    $found = [regex]::Matches($log, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($found.Count -gt 0) { $url = $found[$found.Count - 1].Value }
}
if (-not $url) {
    Write-Host "No link yet. See what happened with:" -ForegroundColor Yellow
    Write-Host "  docker compose -f docker-compose.yml -f docker-compose.tunnel.yml logs tunnel"
    exit 1
}

# The link answers once the API is up (the first start after a build can take a minute).
for ($i = 0; $i -lt 30; $i++) {
    try { if ((Invoke-WebRequest "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { break } } catch { }
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "  Public link:  $url" -ForegroundColor Green
Write-Host "  SMS inbox (OTPs, only on this computer):  http://localhost:8025"
Write-Host ""
Write-Host "Share the link with anyone; it works on phones (GPS and microphone need this HTTPS link)."
Write-Host "It stays on while this computer and Docker are running. To stop sharing: stop-share.bat"
try { Set-Clipboard -Value $url; Write-Host "(The link is copied: paste it with Ctrl+V.)" } catch { }
Start-Process $url
