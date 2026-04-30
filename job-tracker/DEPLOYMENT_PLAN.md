# Deploy AppToSync — GCP Compute Engine (Always Free)

## What we're doing
Deploy to a GCP e2-micro VM (Always Free tier — never charged).  
HTTPS via DuckDNS free subdomain + Let's Encrypt.  
SQLite persists on the VM's disk.  
App runs via systemd so it survives reboots.

**Target URL:** `https://apptosync.duckdns.org`  
**Gmail OAuth redirect:** `https://apptosync.duckdns.org/gmail/callback`

---

## Code changes already done
- `app/config.py` — `GMAIL_REDIRECT_URI` reads from env var (localhost fallback)
- `app/database.py` — WAL mode enabled on every connection (prevents write locks)
- `app/extensions.py` — Flask-Limiter instance
- `app/routes.py` — `/gmail/sync` rate-limited to 6 per minute
- `main.py` — binds `0.0.0.0`, respects `PORT`, `app` at module scope, polls only if `GMAIL_AUTO_POLL=true`
- `pyproject.toml` + `uv.lock` — `gunicorn`, `Flask-Limiter` added
- `Dockerfile` — 1 worker + 4 threads (better for SQLite)
- `.env.example` — placeholder for local onboarding

---

## Step 1 — Reserve a static external IP

In GCP Console → VPC network → External IP addresses:
- Click **Reserve a static address**
- Name: `apptosync-ip`
- Region: `us-central1` (must match VM region for Always Free)
- Click **Reserve**
- Note the IP address (e.g. `34.x.x.x`)

34.30.110.163

---

## Step 2 — Create the VM

GCP Console → Compute Engine → VM instances → Create instance:

| Field | Value |
|---|---|
| Name | `apptosync` |
| Region | `us-central1` |
| Zone | `us-central1-a` |
| Machine type | `e2-micro` |
| Boot disk | Debian 12, 30 GB standard persistent disk |
| External IP | Select `apptosync-ip` (the one you reserved) |
| Firewall | Check **Allow HTTP traffic** and **Allow HTTPS traffic** |

Click **Create**.

---

## Step 3 — Set up DuckDNS

1. Go to https://www.duckdns.org and sign in with your Google account
2. Create a subdomain: `apptosync`
3. Set the IP to the static IP from Step 1
4. Note your DuckDNS token (shown on the dashboard)

Your app will be reachable at `https://apptosync.duckdns.org` after DNS propagates.

---

## Step 4 — SSH into the VM

```bash
gcloud compute ssh apptosync --zone us-central1-a
```

All remaining steps run **inside the VM**.

---

## Step 5 — Install dependencies

```bash
sudo apt update && sudo apt install -y git nginx python3-certbot-nginx curl

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

---

## Step 6 — Clone the repo and install

```bash
git clone https://github.com/achibukz/JobPilot.git
cd JobPilot/job-tracker
uv sync --frozen
```

---

## Step 7 — Create the .env file

```bash
nano .env
```

Paste and fill in:

```
SECRET_KEY=<run: python3 -c 'import secrets;print(secrets.token_hex(32))'>
DATABASE_PATH=/home/<your-username>/JobPilot/job-tracker/job_tracker.db
GEMINI_API_KEY=<your key>
GROQ_API_KEY=<your key>
GMAIL_CLIENT_ID=<your client id>
GMAIL_CLIENT_SECRET=<your client secret>
GMAIL_REDIRECT_URI=https://apptosync.duckdns.org/gmail/callback
SEED_DEMO_DATA=false
GMAIL_AUTO_POLL=false
PORT=8080
```

---

## Step 8 — Create systemd service

```bash
sudo nano /etc/systemd/system/apptosync.service
```

Paste (replace `<your-username>` and `<path-to-uv>`):

```ini
[Unit]
Description=AppToSync
After=network.target

[Service]
User=<your-username>
WorkingDirectory=/home/<your-username>/JobPilot/job-tracker
ExecStart=/home/<your-username>/.local/bin/uv run gunicorn --workers 1 --threads 4 -b 0.0.0.0:8080 main:app
Restart=always
RestartSec=5
EnvironmentFile=/home/<your-username>/JobPilot/job-tracker/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable apptosync
sudo systemctl start apptosync
sudo systemctl status apptosync   # should show: active (running)
```

---

## Step 9 — Configure nginx

```bash
sudo nano /etc/nginx/sites-available/apptosync
```

Paste:

```nginx
server {
    listen 80;
    server_name apptosync.duckdns.org;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/apptosync /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 10 — Get HTTPS certificate

```bash
sudo certbot --nginx -d apptosync.duckdns.org
```

Follow the prompts. Certbot will auto-configure nginx for HTTPS and set up auto-renewal.

---

## Step 11 — Update Google Cloud Console

In your existing GCP project (the one with Gmail OAuth):

1. APIs & Services → Credentials → your OAuth 2.0 Client
2. **Authorized redirect URIs** → Add: `https://apptosync.duckdns.org/gmail/callback`
3. Keep `http://127.0.0.1:3000/gmail/callback` for local dev
4. **Test users** → Add Gmail addresses of your friends (up to 100)

---

## Step 12 — Verify

```bash
curl https://apptosync.duckdns.org/api/health
# → {"status": "ok", "service": "job-tracker"}
```

Then visit `https://apptosync.duckdns.org` in a browser:
- Index page renders
- Connect Gmail → completes OAuth, returns to app
- Trigger sync → emails parsed, application rows appear
- Redeploy (`git pull && sudo systemctl restart apptosync`) → data survives

---

## Redeploying after code changes

```bash
cd ~/JobPilot/job-tracker
git pull
uv sync --frozen
sudo systemctl restart apptosync
```

---

## Out of scope
- Public OAuth verification (only needed beyond ~100 users)
- Postgres migration (SQLite + VM disk is sufficient)
- CI/CD auto-deploy
- Custom domain (DuckDNS subdomain chosen)


  ---                                                                                                                             
  Where the app lives                                                                                                             
                                                                                                                                  
  Your app runs on a Google Cloud Platform (GCP) virtual machine — basically a small computer in Google's data center that stays  
  on 24/7. It's an e2-micro instance (the free tier), named apptosync, located in us-central1-a (Iowa).                           
                                                                          
  That machine has your code, your SQLite database, and it runs your Flask app continuously.                                      
                                                                          
  ---                                                                                                                             
  What gcloud compute ssh does                                            
                                                                       
  gcloud compute ssh apptosync --zone us-central1-a --command "..."

  - gcloud — Google's CLI tool installed on your Mac                                                                              
  - compute ssh — SSH into a GCP virtual machine (like logging into a remote computer)
  - apptosync — the name of your VM                                                                                               
  - --zone us-central1-a — tells GCP which datacenter the VM is in                                                                
  - --command "..." — instead of opening an interactive terminal, just run this one command and exit                              
                                                                                                                                  
  So this one line is equivalent to you manually SSHing into the server and typing commands.                                      
                                                                                                                                  
  ---                                                                                                                             
  What the deploy commands do, one by one                                 
                                                                       
  cd ~/JobPilot/job-tracker              
  Navigate to the project folder on the VM.
                                                                                                                                  
  git pull
  Download the latest code from GitHub onto the VM. This is how the new code gets onto the server.                                
                                                                                                                                  
  ~/.local/bin/uv sync --frozen                                        
  Make sure Python dependencies are up to date. --frozen means "don't upgrade anything, just install exactly what's in the        
  lockfile." We use the full path (~/.local/bin/uv) because SSH sessions don't load your full shell profile, so uv isn't on the   
  PATH by default.                                                     
                                                                                                                                  
  sudo systemctl restart apptosync                                        
  Restart the app. systemd is Linux's process manager — it keeps your app running as a background service. Restarting it picks up
  the new code. Without this, the old code would still be running in memory even after git pull.                                  
   
  ---                                                                                                                             
  The full flow in one picture                                            
                                                                       
  Your Mac  →  git push  →  GitHub       
                                ↓                                                                                                 
                          gcloud compute ssh
                                ↓                                                                                                 
                             GCP VM                                       
                            git pull   ← pulls from GitHub                                                                        
                            uv sync    ← installs deps                    
                      systemctl restart ← reloads the app                                                                         
                                ↓        
                      Users visit apptosync.duckdns.org                                                                           
                                                                          
  ---                                                                                                                             
  Why duckdns.org?                                                        
                                                                                                                                  
  GCP free-tier VMs don't get a permanent IP — it can change if the VM restarts. DuckDNS is a free dynamic DNS service that maps a
   fixed domain name (apptosync.duckdns.org) to whatever the current IP of the VM is, so the URL always works.    