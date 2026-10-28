# Deploy Janamdatri-AI on Microsoft Azure (Azure for Students)

The app runs on one small Ubuntu VM with Docker: PostgreSQL, Redis, the API and workers, the SMS inbox (Mailpit)
and Caddy for free automatic HTTPS. The AI runs on cloud APIs (Gemini, Groq, Azure Speech), so the VM needs no GPU.
With HTTPS, phones can use GPS and the microphone, and anyone with the link (the jury) can open the app.

**Time:** about 45 minutes the first time. **Cost:** the $100 student credit; no credit card.

---

## 1. Protect your credit (5 min)

1. Sign in at [portal.azure.com](https://portal.azure.com) with the account where you activated **Azure for Students**.
2. **Cost Management + Billing → Budgets → + Add**: amount **$25** per month, alerts at **50 %, 80 %, 100 %** to your e-mail.
3. Azure for Students has no card: when the credit is used up, resources stop instead of charging you.

## 2. Create the VM (10 min)

**Virtual machines → + Create → Azure virtual machine**

| Setting | Value |
|---|---|
| Region | **(Asia Pacific) Central India** (data stays in India) |
| Image | **Ubuntu Server 24.04 LTS** |
| Size | **B2s** (2 vCPU, 4 GB RAM). The free B1s (1 GB) is too small for the full stack. |
| Authentication | **SSH public key** → *Generate new key pair* → name `janamdatri-key` |
| Username | `azureuser` |
| Inbound ports | **SSH (22), HTTP (80), HTTPS (443)** |
| Disk | Standard SSD, 30 GB (default) |

**Review + create → Create → Download private key** and keep the `.pem` file safe (it is your login).

## 3. Give the VM a web address (2 min)

VM → **Overview → DNS name → Not configured** (or Public IP → Configuration) → **DNS name label**: `janamdatri`
→ **Save**. Your address is now `janamdatri.centralindia.cloudapp.azure.com`. This is your **DOMAIN**.

## 4. Save credit while you are not demoing (2 min)

- VM → **Operations → Auto-shutdown**: On, 11:30 PM, time zone (UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi.
- Between demos: VM → **Stop**. A stopped (deallocated) VM costs only its disk (a few dollars a month).
  Start it again from the portal before a demo; the address stays the same.

## 5. Connect and install Docker (10 min)

In **PowerShell** on your PC (use the folder where the `.pem` file is):

```powershell
ssh -i .\janamdatri-key.pem azureuser@janamdatri.centralindia.cloudapp.azure.com
```

On the VM:

```bash
git clone https://github.com/nitishgautam48/janamdatri-ai.git
cd janamdatri-ai
bash deploy/vm-setup.sh
newgrp docker
```

For a **private** repository, `git clone` asks for your GitHub username and a **personal access token** as the password
(github.com → Settings → Developer settings → Personal access tokens → Fine-grained → read access to this repository).

## 6. Create the settings file on the VM (10 min)

```bash
cp .env.example .env
openssl rand -hex 32        # copy the output: it is your JWT_SECRET
nano .env
```

Fill in (keys from the provider consoles; secrets you make up yourself):

```env
GEMINI_API_KEY=...
GROQ_API_KEY=...
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=centralindia
JWT_SECRET=<the openssl output>
ADMIN_PASSWORD=<a strong password>
POSTGRES_PASSWORD=<another strong password>
DOMAIN=janamdatri.centralindia.cloudapp.azure.com
MAILPIT_UI_AUTH=nitish:<a strong password>
```

Save with **Ctrl+O, Enter**, exit with **Ctrl+X**. The `.env` file stays on the VM only; it is never committed.

## 7. Start (5–10 min for the first build)

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

Open:

- **App:** `https://janamdatri.centralindia.cloudapp.azure.com`
- **Status of every AI provider and the free limits:** `https://…/health`
- **SMS inbox (OTP codes):** `https://…/mailpit/`; log in with `MAILPIT_UI_AUTH`

The HTTPS certificate is issued automatically on the first visit (ports 80 and 443 must be open).

## 8. Update after pushing new code to GitHub

```bash
cd ~/janamdatri-ai && bash deploy/update.sh
```

---

## Optional Azure services

**Azure AI Speech (free F0):** natural Hindi voices and speech-to-text. *Create a resource → Speech → Region Central
India → Pricing tier Free F0 → Keys and Endpoint → KEY 1* → `AZURE_SPEECH_KEY`. The app keeps usage under the free
5 audio hours and 500,000 characters a month (`AZURE_STT_MONTHLY_SECONDS`, `AZURE_TTS_MONTHLY_CHARS`).

**Azure Maps (more accurate hospitals and road routes):** *Create a resource → Azure Maps Account → Pricing tier Gen2
→ Authentication → Primary Key* → `AZURE_MAPS_KEY`. Without it the app uses OpenStreetMap and the free OSRM router.
Results are cached for a week, so usage stays low.

## Troubleshooting

| Problem | Fix |
|---|---|
| Browser says the site is not secure / no certificate | Check that the VM allows inbound 80 and 443 (VM → Networking) and that `DOMAIN` in `.env` matches the DNS name exactly. Then `docker compose -f docker-compose.yml -f docker-compose.prod.yml restart caddy`. |
| App refuses to start, log says "insecure settings" | Set a long random `JWT_SECRET` and a new `ADMIN_PASSWORD` in `.env`. |
| `/health` shows "no key" for a provider | The key is missing or misspelt in `.env`; fix it and run `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`. |
| Answers come from the knowledge base only | The daily free limit (`AI_DAILY_LIMIT`) is used up or every key failed; `/health → limits` shows today's count. |
| Logs | `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f api` |
