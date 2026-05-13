# BeeCount Cloud &nbsp; [中文](./README.md)

[![Docker Pulls](https://img.shields.io/docker/pulls/sunxiao0721/beecount-cloud)](https://hub.docker.com/r/sunxiao0721/beecount-cloud)
[![License](https://img.shields.io/badge/license-BSL-blue)](./LICENSE)

**Self-hosted sync cloud for the [BeeCount](https://github.com/TNT-Likely/BeeCount) personal accounting app.** Keep iOS / Android / Web books on one ledger you fully own — no ads, no subscription, no third-party lock-in.

![BeeCount Cloud Web console](./docs/screenshot-en.png)

---

## 🤔 Why BeeCount Cloud

### vs Other Self-Hosted Ledgers

| Item | BeeCount Cloud | Firefly III | Actual Budget | Maybe Finance |
|---|---|---|---|---|
| Native mobile apps | ✅ iOS + Android | ❌ Web only | ⚠️ Web PWA | ❌ Web only |
| Real-time multi-device sync | ✅ WebSocket sub-sec | ❌ None | ⚠️ File sync | ❌ None |
| AI bookkeeping | ✅ AI / OCR / voice | ❌ | ❌ | ⚠️ Partial |
| Deployment cost | Single container + 1 volume | Container + Postgres | Container + file storage | Multi-service |
| Encrypted backup | ✅ AES-256 + multi-remote | ⚠️ Manual | ❌ | ⚠️ Manual |
| Chinese / i18n | ✅ Simp/Trad/EN | ⚠️ Partial | ❌ | ❌ |

### vs Commercial Cloud Ledgers

- 🔐 **Privacy first** — Data never leaves your server, developer can't access
- 💰 **Completely free** — Cost = one Docker volume, no subscription
- 🛡️ **No lock-in** — Data = SQLite + attachment files, walk away anytime
- 🔓 **Auditable code** — Fully open-source, FastAPI + React in repos

---

## ✨ Features

### Core Sync

- **Two-way realtime sync** — mobile / web changes land on other devices within ~2 seconds via WebSocket
- **Offline-first** — mobile app writes locally, reconciles on reconnect; conflicts resolved by deterministic Last-Write-Wins with device tie-break
- **Entity-level diff** — transactions / accounts / categories / tags / budgets each tracked individually, no full-snapshot overwrites
- **Auto session recovery** — token refresh failures auto-retry with stored credentials; devices stay online across network hiccups
- **Deep health check** — pull-to-refresh compares local vs remote counts and self-repairs differences

### Bookkeeping

- **Multi-ledger** with per-ledger currency
- **Transactions** — income / expense / transfer, multiple accounts, categories, tags, attachments
- **Budgets** — per-category or total, monthly / yearly period
- **Recurring transactions** (mobile)
- **CSV import / export** (mobile, supports Alipay / WeChat bills)
- **Rich analytics** — monthly trends, category breakdowns, year heatmap, savings rate, top tags / accounts

### Appearance & Preferences (synced)

- Theme color, income/expense color scheme, avatar, display name
- Month display format, compact amount, transaction time visibility
- AI provider configs + custom prompts (mobile AI integration)

### Web Console

- Full bookkeeping UI (transactions / accounts / categories / tags / budgets)
- Interactive dashboard (mobile-like, responsive)
- **⌘K command palette + AI doc Q&A** — ⌘K (macOS) / Ctrl+K from anywhere; type `?xxx` or pick "Ask AI" to RAG-retrieve from official docs and stream an answer using your App-configured LLM, with source links auto-attached
- **PWA support** — Click the install icon in your browser's address bar to install as a desktop app; works offline with cached data
- Trilingual — 简体中文 / 繁體中文 / English
- Dark / light mode with personalized primary color
- Admin panel — devices, health, sync errors, backup artifacts, **live server logs**

### Admin & Ops

- In-memory ring buffer log viewer (level / source / keyword filter + auto-refresh)
- Device session list, online indicator, forced signout
- `/metrics` Prometheus endpoint, `/ready` health probe

---

## 🔐 Backup System

> **Multi-remote + AES-256 encrypted backup**, built into the single container. Local DB / attachments / JWT secret can be auto-pushed to any S3 / R2 / B2 / WebDAV / Google Drive / OneDrive on a schedule.

### Highlights

- **rclone multi-remote fan-out** — One backup pushed in parallel to multiple remotes for redundancy
- **AES-256 encrypted zip** — Backup files are standard password-protected ZIP archives. **Download from your bucket, double-click → macOS Archive Utility / 7-Zip / Keka / WinRAR auto-prompts for password → extracted** (recoverable even without BeeCount service)
- **APScheduler cron** — Standard cron expressions, timezone follows container `TZ`
- **Retention cleanup** — Keep last N days, old ones auto-deleted, `keep_at_least=1` defends against misconfiguration
- **Web 3-tab UI**:
  - **Remotes** — S3 / R2 / B2 / WebDAV form, 32-byte passphrase one-click generation
  - **Schedules** — cron presets, multi-remote selection, "run now"
  - **History & progress** — live upload progress, history, one-click Restore
- **Restore isolation** — Server-side restore **never touches live data**, only writes to `<DATA_DIR>/restore/<run_id>/`, user manually cp/rsync to replace
- **Audit log** — Every create / test / reveal / backup run is audit-logged

### Default Recommendation

- **Daily 4 AM local time**, 30-day retention, encrypted + include attachments
- **2 redundant remotes** (e.g. R2 + WebDAV) — one fails, other survives

### Disaster Recovery

As long as you have the `.zip` file + passphrase, any system, any standard archive tool can extract the backup — no dependency on BeeCount service.

---

## 📸 Screenshots

| 中文 UI | English UI |
|---------|------------|
| ![ZH](./docs/screenshot-zh.png) | ![EN](./docs/screenshot-en.png) |

---

## 🚀 Docker Compose Deployment

The prebuilt image [`sunxiao0721/beecount-cloud`](https://hub.docker.com/r/sunxiao0721/beecount-cloud) bundles FastAPI backend + Web console — single container + one data volume.

### 1) Create `docker-compose.yml`

```yaml
services:
  beecount-cloud:
    image: sunxiao0721/beecount-cloud:latest
    restart: unless-stopped
    ports:
      - "8869:8080"
    volumes:
      - ./data:/data
    environment:
      # —— Optional: enable ⌘K "AI Doc Q&A" (RAG over official docs) ——
      # Leaving the key empty is fine — the feature falls back to "open docs site
      # search". All other features keep working regardless.
      # Default uses SiliconFlow's free tier (~100k queries/month for self-hosters).
      # Register at https://siliconflow.cn to grab a free key.
      EMBEDDING_BASE_URL: https://api.siliconflow.cn/v1
      EMBEDDING_MODEL: BAAI/bge-m3
      EMBEDDING_API_KEY: ""        # ← fill in your SiliconFlow key to enable AI Q&A
```

> Full list of compatible embedding providers (SiliconFlow / OpenAI / Zhipu / Aliyun / Doubao / Voyage / Mistral / Jina / Together / self-hosted Ollama / ...) and switching notes — see [`.env.example`](./.env.example). **Key constraint**: `EMBEDDING_MODEL` must match the embedding model used to build the bundled sqlite index (default `BAAI/bge-m3`); switching models requires rebuilding the index on both sides.

### 2) Start

```bash
docker compose up -d
# View first-launch admin credentials:
docker compose logs beecount-cloud | grep -A 10 "first launch"
```

Output similar to:

```
 BeeCount Cloud — first launch, admin account auto-created:

   Email:    owner@example.com
   Password: FIDodUnwprkw1zUi
```

With this account:

- Open `http://<your-server-ip>:8869` in browser → **Web admin console**
- In the App, choose "BeeCount Cloud" and enter the server URL + credentials

### 3) Upgrade

```bash
docker compose pull
docker compose up -d
```

Alembic migrations run automatically on container startup (see [Database Migrations](#️-database-migrations)).

### 4) Backup

The `./data/` directory contains all persistent data: SQLite database, attachments, backup archives, JWT secret. Just tar the directory:

```bash
tar czf beecount-$(date +%F).tar.gz ./data
```

Or recommended: configure the built-in **multi-remote encrypted backup** (see [Backup System](#-backup-system)) for automatic cron pushes to S3 / R2 / WebDAV.

### 5) Custom Initial Admin

```yaml
services:
  beecount-cloud:
    image: sunxiao0721/beecount-cloud:latest
    restart: unless-stopped
    ports:
      - "8869:8080"
    environment:
      # Override default random admin:
      BOOTSTRAP_ADMIN_EMAIL: me@example.com
      BOOTSTRAP_ADMIN_PASSWORD: <strong password>
      # Scheduler timezone (default: Asia/Shanghai, follows container TZ):
      # TZ: Asia/Shanghai
    volumes:
      - ./data:/data
```

### 6) Public Deployment

Front with nginx / caddy for HTTPS + domain. App and Web both support `https://` URLs.

---

## 🗄️ Database Migrations

Schema versions are managed by [Alembic](https://alembic.sqlalchemy.org/).

**On every container startup**, the entrypoint script runs:

```bash
alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8080
```

So after pulling a new image, any new migrations execute in order before requests are served. Data persists in `./data/`, no manual intervention needed.

If a migration fails (rare), the container exits with the database left at the pre-upgrade version — fix the issue and `docker compose pull && up -d` to retry.

---

## 📱 Mobile App Setup

Install [BeeCount](https://github.com/TNT-Likely/BeeCount) (iOS / Android), then:

1. Settings → Cloud Service → BeeCount Cloud
2. Enter server URL (e.g. `https://your-domain.com`) and credentials
3. Enable sync — first sync pushes local data to cloud

---

## 🛠️ Local Development

<details>
<summary>Click to expand setup</summary>

### Requirements

- Python `3.11+`
- Node `20+`, pnpm `9+`

### First-time Install

```bash
make setup-backend
pnpm -C frontend install
```

### Local Start

```bash
# Terminal 1 — API (port 8080)
make migrate
make dev-api

# Terminal 2 — Web dev server (port 5173)
make dev-web
```

### Demo Account

```bash
make seed-demo
# Email: owner@example.com  Password: 123456
```

### Tests

```bash
make test        # pytest
make lint        # ruff
make typecheck   # mypy
pnpm -C frontend/apps/web test:unit
pnpm -C frontend/apps/web exec tsc --noEmit --skipLibCheck
```

### One-shot

```bash
make dev-up
```

### Frontend Package Layout

- `frontend/apps/web` — shell, routing, page composition
- `frontend/packages/api-client` — HTTP + typed responses
- `frontend/packages/web-features` — business panels, permissions, formatting
- `frontend/packages/ui` — shadcn-style base (Radix)

### Build Docker Image

```bash
docker build -t sunxiao0721/beecount-cloud:dev .
docker run -p 8080:8080 -v beecount_data:/data \
  -e JWT_SECRET=dev-secret-at-least-32-bytes-long \
  sunxiao0721/beecount-cloud:dev
```

</details>

---

## 📚 More Documentation

- [Deployment Guide](./docs/DEPLOYMENT.md)
- [Migration & Rollback](./docs/MIGRATION.md)
- [Observability](./docs/OBSERVABILITY.md)
- [Sync Architecture](./docs/SYNC_ARCHITECTURE.md)
- [MCP server (LLM integration)](./docs/MCP.en.md) — Claude Desktop / Cursor / Cline talk to your ledgers via a PAT
- Runtime OpenAPI / Swagger UI: visit `http://your-domain.com/docs`

---

## 📄 License

This project uses the **Business Source License (BSL)**.

| Use Case | License |
|---|---|
| ✅ **Personal self-hosting** | Completely free |
| ✅ **Learning / research** | Completely free |
| ✅ **Open-source contribution** | Welcome |
| ❌ **Commercial use** | Requires paid license |

**What counts as commercial use**:

- Operating BeeCount Cloud as a commercial SaaS for customers
- Deploying in a for-profit organization
- Providing paid cloud services based on this software
- Reselling or integrating into a paid product

For commercial licensing, contact via [GitHub Issues](https://github.com/TNT-Likely/BeeCount-Cloud/issues). See [LICENSE](./LICENSE).

---

## 🔗 Links

- Mobile App: <https://github.com/TNT-Likely/BeeCount>
- Docker Hub: <https://hub.docker.com/r/sunxiao0721/beecount-cloud>
- Issue Tracker: <https://github.com/TNT-Likely/BeeCount-Cloud/issues>
