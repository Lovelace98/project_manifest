# Dokploy Deployment Guide for Hostinger VPS

This guide walks you through deploying the unified **Blueprint DNA Operations Super App** (combining **Manifest Portal** and **Report Bot**) to a **Hostinger VPS** using **Dokploy**.

---

## 🏗️ Architecture Overview

* **Server:** Hostinger VPS (Ubuntu 24.04 LTS)
* **PaaS Manager:** [Dokploy](https://dokploy.com) (handles Docker containers, Traefik reverse proxy, and Let's Encrypt SSL)
* **Application:** FastAPI + Uvicorn (ASGI async runner with LibreOffice Writer, PyMuPDF, and ephemeral in-memory JobStore)
* **Port:** `5000`
* **Health Check:** `/health`
* **Apps Included:**
  * 📋 **Manifest Portal:** Live Notion batch sync, parent-before-child ordering, and Excel generator.
  * 📑 **Report Bot:** Eurofins/UFC/Hong Kong PDF extraction, legal verification pages, rebranding, and AES-256 encryption.

---

## 📋 Prerequisites

1. A **Hostinger VPS** (KVM 1 or KVM 2 with at least 2GB RAM is recommended).
2. A domain or subdomain (e.g. `manifest.yourdomain.com`) pointing to your Hostinger VPS IP via a DNS **A Record**.
3. Your Notion Integration Secret (`NOTION_TOKEN`) and Database IDs (`NOTION_PARTICIPANT_DB_ID`, `NOTION_CASES_DB_ID`).

---

## Step 1: Set Up Dokploy on Hostinger VPS

Hostinger supports two installation methods:

### Option A: Using the Hostinger 1-Click OS Template (Easiest)
1. Go to your **Hostinger hPanel** → **VPS**.
2. Select your VPS → Go to **OS & Panel** → **Operating System**.
3. Select **Applications** or **OS with Panel** → choose **Ubuntu 24.04 with Dokploy**.
4. Set your root password or SSH key, and confirm installation.
5. Once complete, navigate to `http://<YOUR_VPS_IP>:3000` in your web browser.

### Option B: Clean Ubuntu 24.04 VPS (Manual Script)
If you already have a running Ubuntu VPS:
1. SSH into your VPS:
   ```bash
   ssh root@<YOUR_VPS_IP>
   ```
2. Run the official Dokploy installer:
   ```bash
   curl -sSL https://dokploy.com/install.sh | sh
   ```
3. Open ports in your firewall if UFW is active:
   ```bash
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw allow 3000/tcp
   sudo ufw reload
   ```
4. Access the dashboard at `http://<YOUR_VPS_IP>:3000`.

### Initial Dokploy Setup
* Open `http://<YOUR_VPS_IP>:3000`.
* Create your Dokploy admin username, email, and password.

---

## Step 2: Push Your Code to Git (GitHub / GitLab)

If this repository is not yet connected to a Git remote:

1. Initialize Git in the project root:
   ```bash
   cd "/Users/ghost/Documents/Projects/Project Manifest"
   git init
   git add .
   git commit -m "feat: prepare project for Dokploy deployment"
   ```
2. Create a new repository on GitHub (e.g. `project-manifest`, private recommended).
3. Push your repository:
   ```bash
   git remote add origin git@github.com:<your-username>/<your-repo-name>.git
   git branch -M main
   git push -u origin main
   ```
*(Sensitive files like `.env`, participant `.docx`, and `.xlsx` are already excluded by `.gitignore`)*

---

## Step 3: Connect Git in Dokploy

1. In Dokploy, go to **Settings** → **Server** or **Git Providers**.
2. Connect your **GitHub** account (via GitHub App or Personal Access Token / Deploy SSH Key).

---

## Step 4: Create and Configure the Application

1. In the Dokploy sidebar, click **Projects** → **Create Project** (Name: `Blueprint Manifest`).
2. Inside your project, click **Create Service** → Select **Application**.
3. Configure the Application details:
   * **Name:** `manifest-app`
   * **Source Type:** `GitHub` (or Git)
   * **Repository:** Select your `project-manifest` repo
   * **Branch:** `main`
   * **Build Type:** Select **Dockerfile**
   * **Dockerfile Path:** `/Dockerfile`
   * **Context Path:** `/`

---

## Step 5: Configure Production Environment Variables

In your application's settings, go to the **Environment** tab and paste your production environment variables:

```env
# Unified Web Authentication & Session
ADMIN_USERNAME=admin
ADMIN_PASSWORD=SetAStrongProductionPasswordHere!
SECRET_KEY=paste_a_secure_random_64_char_key_here
SESSION_TTL_HOURS=12
SESSION_COOKIE_SECURE=true

# Report Bot Configuration
REPORT_BOT_DEV_PASSWORD=SetAStrongProductionPasswordHere!
REPORT_BOT_PDF_OWNER_PASSWORD=at-least-16-chars-owner-secret

# Notion API Configuration
NOTION_TOKEN=ntn_your_actual_notion_secret_token
NOTION_PARTICIPANT_DB_ID=281dbd83-c9c2-4342-8a93-92574ef8ed15
NOTION_CASES_DB_ID=382c226a-2e86-48f1-b70e-c4e9e8f62d1d
NOTION_CACHE_TTL_SECONDS=180

# Port & Server
PORT=5000
```

> 💡 **Tip:** Generate a secure `SECRET_KEY` by running:
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

Click **Save** in the Environment tab.

---

## Step 6: Configure Custom Domain and SSL (HTTPS)

Dokploy uses Traefik to route traffic and issue automatic SSL certificates via Let's Encrypt.

1. Ensure your domain's DNS has an **A Record**:
   * **Host / Name:** `manifest` (or `@` for root)
   * **Points to:** `<YOUR_HOSTINGER_VPS_IP>`
2. In Dokploy, navigate to your application's **Domains** tab.
3. Click **Add Domain**:
   * **Host:** `manifest.yourdomain.com`
   * **Path:** `/`
   * **Container Port:** `5000`
   * **HTTPS:** Toggle **ON** (Let's Encrypt certificate will be requested automatically)
4. Click **Save**.

---

## Step 7: Deploy and Verify

1. Click the **Deploy** button in the top right.
2. Watch the live build logs in the **Deployments** tab. Dokploy will:
   * Clone the latest commit from Git.
   * Build the Docker image according to the `Dockerfile`.
   * Start the container and attach it to Dokploy's internal network.
   * Verify the `/health` endpoint status.
   * Attach the Traefik routing rules and SSL certificate.
3. Once deployed:
   * Visit `https://manifest.yourdomain.com/health` → Should return `{"service": "blueprint-manifest", "status": "healthy"}`.
   * Visit `https://manifest.yourdomain.com` → Should redirect to `/login` with full HTTPS encryption.

---

## Step 8: Enable Auto-Deploy (CI/CD)

1. In the application settings, go to the **General** tab.
2. Enable **Auto Deploy**.
3. Dokploy will set up a GitHub webhook. Any new `git push origin main` will automatically trigger a zero-downtime build and redeployment.

---

## 🔍 Troubleshooting & Operational Tips

### Viewing Application Logs
* Open your application in Dokploy → Go to the **Logs** tab to view real-time Gunicorn request logs and any Notion API responses.

### Restarting the Application
* In Dokploy, click **Actions** → **Restart** (or trigger a clean rebuild).

### Rate Limiting Behind Traefik
* The application uses `ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)` so each visitor's real IP is correctly detected for login rate limits (5 attempts / min) and Notion sync limits (30 calls / min).

### Backups
* In the Dokploy sidebar, check the **Backups** tab to configure automated backups of your server configurations and Dokploy database.
