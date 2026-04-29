# Job Tracker Prototype

Local first prototype for the job application tracker described in the PRD and design docs.

## What it includes

- Dashboard with add, edit, delete, filtering, and overdue follow-up highlighting
- SQLite persistence
- Model-agnostic email parser preview with a local heuristic provider plus switchable provider names for future Gemini/OpenAI wiring
- REST API endpoints for applications and health checks
- Basic automated tests
- GitHub Actions CI workflow ready to add

## Run locally

```bash
uv sync
uv run python main.py
```

## Install dependencies

Use `uv run` so the packages install into the project's environment (required to access Gemini/API keys correctly):

```bash
uv run -m pip install -q -U google-genai python-dotenv
```

Open `http://127.0.0.1:3000` in your browser.

## Run tests

```bash
uv run python -m unittest discover -s tests
```

## Environment variables

```bash
DATABASE_PATH=job_tracker.db
SEED_DEMO_DATA=true
AI_PROVIDER=local
PROVIDER_API_KEY=
```

## Prototype notes

- `local` AI parsing currently uses heuristic extraction so the app runs without paid APIs.
- You can later wire `gemini` or `openai` into the parser adapter without changing the dashboard or API contract.
- The PRD/design still call for a Node/React implementation eventually; this prototype proves the workflow and core data model first.