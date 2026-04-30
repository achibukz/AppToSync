# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the app locally (http://127.0.0.1:5000 by default, or set PORT=3000)
uv run python main.py

# Run all tests (use python -m pytest, not uv run pytest — path resolution differs)
uv run python -m pytest tests/

# Run a single test file
uv run python -m pytest tests/test_api_routes.py

# Run a specific test
uv run python -m pytest tests/test_api_routes.py::ApiRouteTests::test_api_status_update
```

## Deployment

Deployed on a GCP e2-micro VM (Always Free) at **https://apptosync.duckdns.org**.  
Runs via `gunicorn` (1 worker, 4 threads) managed by `systemd`. SQLite on the VM disk.

### Redeploy after code changes

```bash
# SSH into the VM
gcloud compute ssh apptosync --zone us-central1-a

# Then on the VM:
cd ~/JobPilot/job-tracker
git pull
uv sync --frozen
sudo systemctl restart apptosync
```

Health check: `curl https://apptosync.duckdns.org/api/health`

## Architecture

**JobPilot** is a multi-user job application tracker with Gmail OAuth integration, AI-powered email parsing (Gemini + Groq), and a pending-review queue for parsed emails. Flask serves a Jinja2 dashboard with a tabbed UI (Dashboard + Emails tabs).

### Module responsibilities

| File | Role |
|------|------|
| `main.py` | Entry point — creates Flask app, binds `0.0.0.0:PORT`, starts Gmail polling thread if `GMAIL_AUTO_POLL=true` |
| `app/__init__.py` | App factory — initialises DB schema, registers routes |
| `app/config.py` | Constants: `STATUS_OPTIONS`, `SOURCE_OPTIONS`, `PARSER_MODEL_CHOICES`, Gmail scopes, demo seed data |
| `app/database.py` | SQLite schema creation, connection management, WAL mode |
| `app/models.py` | Pure CRUD for `applications` table |
| `app/auth.py` | User registration, login, password hashing, `login_required` decorator |
| `app/routes.py` | All Flask routes: web (HTML) + REST API (`/api/`) |
| `app/email_parser.py` | Dual-provider parsing: Gemini AI + Groq + local regex heuristics fallback |
| `app/gmail.py` | Gmail OAuth flow, background polling thread, sync pipeline, `retry_parse_email` |
| `app/parsed_emails.py` | CRUD for `parsed_emails` table — pending-review / paused / accepted / dismissed states |
| `app/watchers.py` | Per-application sender email/domain watchers for auto-routing emails |
| `app/validators.py` | Input validation & normalization |
| `app/extensions.py` | Flask-Limiter instance |
| `app/utils.py` | `utc_now()` and shared utilities |

### Database

SQLite (`job_tracker.db`) with WAL mode. Tables:

| Table | Purpose |
|---|---|
| `users` | Registered accounts — `id`, `email`, `password_hash` |
| `applications` | Core job tracking records, scoped by `user_id` |
| `gmail_tokens` | Per-user OAuth tokens + sync metadata (`last_sync_at`, `last_sync_error`) |
| `parsed_emails` | Emails fetched from Gmail awaiting review or already actioned |
| `application_watchers` | Sender patterns tied to an application for auto-routing |

All data is per-user — no row is accessible across accounts.

Key columns on `parsed_emails`: `parse_status`, `parsed_company`, `parsed_role`, `parsed_status`, `parsed_source`, `parsed_applied_date`, `parsed_confidence`, `parsed_reasoning`, `application_id`.

### Gmail sync pipeline

`gmail.py` runs a daemon thread that wakes every 60 s and calls `sync_gmail_messages()` when the sync interval has elapsed. That function:

1. Fetches Gmail messages since last sync
2. Decodes Base64 body, strips HTML
3. Checks `application_watchers` — if a sender matches a watcher, auto-routes the email to that application (status-only update, no review queue)
4. Falls back to fuzzy company+role match for auto-updates on known applications
5. On auto-update: sets `applied_date` from `parsed_applied_date` when status is Applied; sets `source` from `parsed_source` only if the existing value is blank or "Other"
6. Unrecognised job emails go to `pending_review` in `parsed_emails`
7. Parse failures (quota, timeout) set status to `paused` for manual retry
8. `_choose_status()` ensures status only moves *forward* in the pipeline

### Email parser

`email_parser.py` exposes `parse_job_email(text, provider, gemini_model, groq_model)`. Returns structured JSON: `is_job_related`, `company`, `role`, `status`, `source`, `applied_date`, `confidence`, `reasoning_summary`. On any API failure it falls back to local regex heuristics automatically.

Parser model choices are configured in `config.py` as `PARSER_MODEL_CHOICES` (list of `(value, label, provider, model)` tuples) with `DEFAULT_PARSER_CHOICE`. The retry UI lets users pick a specific model before re-parsing a paused email.

### Status pipeline order

`Applied (0) → Interview Scheduled (1) → Technical Test (2) → Final Interview (3) → Offer Received (4) → Rejected (5) → Ghosted (6)`

Status only advances during auto-merge — never downgrades.

### Source field

`SOURCE_OPTIONS = ["LinkedIn", "Indeed", "Prosple", "Direct", "Other"]`. When "Other" is selected in the modal, a free-text input appears so the user can type a custom source (e.g. "Jobstreet"). The hidden `name="source"` input carries the final value.

When accepting a parsed email, `parsed_source` from the record pre-fills the source field; `parsed_applied_date` pre-fills the applied date (before falling back to `received_at`).

## Environment variables

```bash
SECRET_KEY=                   # Flask session secret (required in production)
DATABASE_PATH=job_tracker.db  # Absolute path on VM: /home/<user>/JobPilot/job-tracker/job_tracker.db
GEMINI_API_KEY=               # Required for Gemini parsing
GROQ_API_KEY=                 # Required for Groq parsing
GMAIL_CLIENT_ID=              # Google OAuth client ID
GMAIL_CLIENT_SECRET=          # Google OAuth client secret
GMAIL_REDIRECT_URI=https://apptosync.duckdns.org/gmail/callback  # localhost fallback: http://127.0.0.1:3000/gmail/callback
OAUTHLIB_INSECURE_TRANSPORT=1 # Local dev only — never set in production
SEED_DEMO_DATA=false
PARSER_PROVIDER=gemini        # default provider: gemini | groq
GMAIL_SYNC_INTERVAL_MINUTES=15
GMAIL_AUTO_POLL=false         # true on production VM only
PORT=8080                     # production port; defaults to 5000 locally
```

## Testing patterns

Tests use `pytest` with `unittest.TestCase` classes. Each test creates its own temp SQLite DB. Gmail API and Gemini/Groq API calls are always mocked — never hit real services.

**Run with `uv run python -m pytest`**, not `uv run pytest` — the latter doesn't add the project root to `sys.path` and fails to import `app`.

Passing test files: `test_api_routes.py`, `test_email_parser.py`.

Failing test files: `test_company_watchers.py`, `test_parsed_emails.py`, `test_gmail_sync.py` — these predate the multi-user migration and don't pass the required `user_id` argument to `insert_application` / `upsert_email_record`.

## Rate limiting

`/gmail/sync` (both web and API routes) is limited to **6 requests per minute** via Flask-Limiter.
