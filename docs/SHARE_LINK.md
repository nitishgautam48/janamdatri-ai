# Share Janamdatri-AI on a public link (free, Cloudflare Tunnel)

Run the app on your own computer and give anyone a secure **https://** link to it, for a live demo, the jury or
testing on your phone. It is free, needs no account and no router settings, and the data stays on your computer.

## Start sharing

1. Open **Docker Desktop** and wait for **Engine running**.
2. In the project folder, double-click **`share.bat`** (Windows) or run `./share.sh` (macOS / Linux).
3. After 20–60 seconds it shows (and copies) a link like **`https://calm-river-1234.trycloudflare.com`** and opens it.

Send that link to anyone. It works on phones too, and because it is HTTPS, GPS (nearby care, SOS) and the microphone
(voice questions) work.

## Good to know

- **The link changes** every time the tunnel restarts (after a reboot, or after stopping it). Run `share.bat` again to
  see the current link.
- **It works while this computer is on**, Docker Desktop is running and the internet is connected. Keep the computer
  awake (Windows Settings → System → Power → Sleep: Never, while presenting).
- **OTP codes stay private:** the SMS inbox (Mailpit) is not shared. Read OTPs on this computer at
  **http://localhost:8025**. People using the shared link see "the OTP is in the SMS inbox on the computer running the
  app". Visitors can use **Continue as Guest** (guide, health questions, nearby care, SOS) without an OTP.
- **Stop sharing:** double-click **`stop-share.bat`**. The app keeps running at http://localhost:8000.
- Share links for referral slips automatically use the public link.

## Without Docker

If you run the app with `run_local.bat` / `run_local.sh`, install cloudflared once
(`winget install --id Cloudflare.cloudflared` on Windows) and run:

```
cloudflared tunnel --url http://localhost:8000
```

## Permanent link (optional)

Quick tunnels are meant for demos and testing. For a link that never changes, add a domain to a free Cloudflare
account and create a named tunnel, or use a cloud server ([DEPLOY_AZURE.md](DEPLOY_AZURE.md)).
