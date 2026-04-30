# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the app (http://127.0.0.1:3000)
uv run python main.py

# Run all tests
uv run python -m unittest discover -s tests

# Run a single test file
uv run python -m unittest tests/test_gmail_sync.py

# Run a specific test method
uv run python -m unittest tests/test_gmail_sync.py.TestGmailSync.test_deduplication
```

## Architecture

**JobPilot** is a local-first job application tracker with Gmail OAuth integration and AI-powered email parsing. Flask serves a single-page Jinja2 dashboard at `localhost:3000`.

### Module responsibilities

| File | Role |
|------|------|
| `main.py` | Entry point — creates Flask app, starts Gmail polling thread |
| `app/__init__.py` | App factory |
| `app/config.py` | Constants: `STATUS_OPTIONS`, `SOURCE_OPTIONS`, Gmail scopes, demo seed data |
| `app/database.py` | SQLite schema creation & connection management |
| `app/models.py` | Pure CRUD for `applications` table (no Flask context) |
| `app/routes.py` | All Flask routes: web (HTML) + REST API (JSON at `/api/`) |
| `app/email_parser.py` | Dual-provider parsing: Gemini AI + local regex heuristics |
| `app/gmail.py` | Gmail OAuth flow, background polling thread, sync pipeline |
| `app/validators.py` | Input validation & normalization |
| `app/utils.py` | Shared utilities |

### Database

SQLite (`job_tracker.db`) with two tables:
- **`applications`** — core job tracking records; `gmail_message_id` has a unique partial index for deduplication
- **`gmail_connections`** — stores OAuth tokens as JSON; always single row (`id = 1`)

### Gmail sync pipeline

`gmail.py` runs a daemon thread (`start_gmail_polling`) that wakes every 60 seconds, and when the configured interval has elapsed, calls `sync_gmail_messages()`. That function:
1. Queries Gmail API for messages since last sync
2. Decodes Base64 email body, strips HTML
3. Passes text to `parse_job_email()` (Gemini or local heuristics fallback)
4. Deduplicates by `gmail_message_id`; fuzzy-matches by company+role for updates
5. Uses `_choose_status()` to only move status *forward* in the pipeline (Applied → Interview → ... → Offer/Rejected)

### Email parser

`email_parser.py` exposes `parse_job_email(text, provider, gemini_model)`. The Gemini provider returns structured JSON (`is_job_related`, `company`, `role`, `status`, `confidence`, `reasoning`); on any API failure it falls back to the local regex parser automatically.

### Status pipeline order

`Applied (0) → Interview Scheduled (1) → Technical Test (2) → Final Interview (3) → Offer Received (4) → Rejected (5) → Ghosted (6)`

Status can only advance, never downgrade, during Gmail sync merges.

## Environment variables

```bash
GEMINI_API_KEY=          # Required for Gemini parsing
GMAIL_CLIENT_ID=         # Google OAuth client ID
GMAIL_CLIENT_SECRET=     # Google OAuth client secret
GMAIL_REDIRECT_URI=http://127.0.0.1:3000/gmail/callback
OAUTHLIB_INSECURE_TRANSPORT=1   # Required for local OAuth
DATABASE_PATH=job_tracker.db
SEED_DEMO_DATA=true
AI_PROVIDER=local|gemini
GMAIL_SYNC_INTERVAL_MINUTES=15
GMAIL_AUTO_POLL=true
```

## Testing patterns

Tests use `unittest` with `unittest.mock`. Each test creates its own temp SQLite database to avoid side effects. Gmail API calls and Gemini API calls are always mocked — never hit real services in tests.
