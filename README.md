# NexaBank — Online Banking System

> Python · FastAPI · SQLite · OpenRouter LLM · HTML/CSS/JS · Docker · GitHub Actions

## Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI (Python 3.12) + SQLite |
| Frontend | Vanilla HTML/CSS/JS (no framework) |
| Auth | JWT (PyJWT) + SHA-256 password hashing |
| AI | OpenRouter Chat Completions API |
| Reverse Proxy | Nginx |
| CI/CD | GitHub Actions → GHCR → SSH deploy |

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USER/nexabank.git
cd nexabank

# Add your OpenRouter key in .env
# OPENROUTER_API_KEY=your_key_here

# Run everything
docker compose up -d

# App available at:
# http://localhost        ← Frontend
# http://localhost:8000   ← Backend API
```

## Default Credentials

| Role | Email | Password |
|------|-------|----------|
| Admin | admin@nexabank.com | admin123 |

## Features

- **Auth** — Register/login with JWT tokens, role-based (CUSTOMER / ADMIN)
- **Accounts** — Open Savings/Current/FD accounts with opening balance
- **Fund Transfer** — NEFT-style transfers with atomicity
- **Transactions** — Full history with DEBIT/CREDIT breakdown
- **Loans** — Apply with auto EMI calculation, admin approval flow
- **AI Chat** — NexaBot powered by OpenRouter with account context
- **Admin Panel** — User management, loan approval/rejection

## API Endpoints

```
POST  /api/auth/register
POST  /api/auth/login
GET   /api/accounts/my
POST  /api/accounts/create
POST  /api/transactions/transfer
GET   /api/transactions/history/{acc_no}
POST  /api/loans/apply
GET   /api/loans/my
GET   /api/admin/users        [ADMIN]
GET   /api/admin/loans        [ADMIN]
PATCH /api/admin/loans/{id}/status  [ADMIN]
POST  /api/chat               [AI]
GET   /actuator/health
```

## Running Tests

```bash
cd backend
pip install -r requirements.txt pytest
pytest tests/ -v
```

12 tests covering auth, accounts, transfers, loans, and health.

## Environment Variables

Create a `.env` file in project root:

```env
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_SITE_URL=http://localhost
OPENROUTER_APP_NAME=NexaBank
```

## CI/CD Setup

Add these GitHub Secrets for deployment:

| Secret | Value |
|--------|-------|
| `DEPLOY_HOST` | Your server IP |
| `DEPLOY_USER` | SSH username |
| `DEPLOY_KEY` | SSH private key |
| `DEPLOY_PATH` | App path on server (optional, default `/opt/nexabank`) |
| `GHCR_USERNAME` | GitHub username for server-side `docker login` (optional for public images) |
| `GHCR_TOKEN` | GitHub PAT with `read:packages` (recommended/private images) |

Pipeline: **push/PR to main** → backend tests → build Docker images → push to GHCR → SSH deploy (main only)

Server prerequisites:

- Docker + Docker Compose plugin installed
- Repository checked out at `DEPLOY_PATH` on the server
- If GHCR packages are private, ensure `GHCR_TOKEN` has `read:packages`

## Project Structure

```
nexabank/
├── backend/
│   ├── main.py              # FastAPI app (all routes)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── tests/
│       └── test_main.py     # 12 pytest tests
├── frontend/
│   ├── index.html           # Full SPA (no framework)
│   └── Dockerfile
├── nginx/
│   └── nginx.conf           # Reverse proxy config
├── .github/
│   └── workflows/
│       └── cicd.yml         # GitHub Actions pipeline
├── docker-compose.yml
└── README.md
```
