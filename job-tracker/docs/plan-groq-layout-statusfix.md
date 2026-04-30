# Implementation Plan — Groq parser, sidebar layout, status badge fix

> Handoff doc: written by Opus 4.7 in plan mode. Sonnet 4.6 should execute this plan from end to end. Stay on `feature/new-ai`.

## Context

JobPilot currently parses Gmail-imported job emails through Gemini only (via `google-genai`). The user wants three changes shipped together:

1. **Add Groq Llama 3.3 as an alternative parser**, selectable from a dropdown next to the existing "Sync now" button. Both Groq models (`llama-3.3-70b-versatile` and `llama-3.1-8b-instant`) must be selectable, alongside the existing two Gemini models.
2. **Restructure the dashboard layout** to a two-column shell: a left sidebar containing the title hero, the Gmail Sync card, and the four stat cards stacked vertically; the applications table fills the right column at full height. Matches the user's wireframe.
3. **Fix the "Interview Scheduled" status badge** which currently wraps to two lines in the table — must render on one line.

`GROQ_API_KEY` is already present in `.env` (line 8). `groq>=1.0.0` is already added to `pyproject.toml` (line 14). No new credentials or dep additions needed beyond what's in the repo right now.

---

## Change list by file

### 1. Groq parser provider

**`app/config.py`** — add constants below the existing `GEMINI_MODEL_OPTIONS` block (around line 26):

```python
GROQ_MODEL_OPTIONS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
DEFAULT_GROQ_MODEL = GROQ_MODEL_OPTIONS[0]

PARSER_PROVIDER_OPTIONS = ["gemini", "groq"]   # "local" stays as silent fallback
DEFAULT_PARSER_PROVIDER = "gemini"

# Flat list driving the single dropdown in the UI.
# Each entry: (value, label, provider, model)
PARSER_MODEL_CHOICES = [
    ("gemini:gemini-2.5-flash",       "Gemini · 2.5 Flash",            "gemini", "gemini-2.5-flash"),
    ("gemini:gemini-2.5-flash-lite",  "Gemini · 2.5 Flash Lite",       "gemini", "gemini-2.5-flash-lite"),
    ("groq:llama-3.3-70b-versatile",  "Groq · Llama 3.3 70B Versatile","groq",   "llama-3.3-70b-versatile"),
    ("groq:llama-3.1-8b-instant",     "Groq · Llama 3.1 8B Instant",   "groq",   "llama-3.1-8b-instant"),
]
DEFAULT_PARSER_CHOICE = "gemini:gemini-2.5-flash-lite"
```

**`app/email_parser.py`** — extend; do not replace existing functions.

- Import: `from groq import Groq` at the top, alongside `from google import genai`. Update the config import to include the new Groq constants.
- Add `normalize_groq_model(model: str | None) -> str` mirroring `normalize_gemini_model()` (validates against `GROQ_MODEL_OPTIONS`, falls back to `DEFAULT_GROQ_MODEL`).
- Add `groq_parse_job_email_with_error(email_text, model=None) -> tuple[dict | None, str | None]` mirroring `gemini_parse_job_email_with_error()` exactly (same prompt, same return shape). Use the Groq SDK:
  ```python
  api_key = os.getenv("GROQ_API_KEY")
  if not api_key:
      return None, "Groq request failed because GROQ_API_KEY was not set."
  selected_model = normalize_groq_model(model)
  try:
      client = Groq(api_key=api_key)
      response = client.chat.completions.create(
          model=selected_model,
          messages=[{"role": "user", "content": prompt}],
          temperature=0,
          response_format={"type": "json_object"},
      )
      text = response.choices[0].message.content
  except Exception as exc:
      return None, f"Groq request failed: {exc}"
  ```
  Reuse the **same prompt** as the Gemini path (extract it into a small helper `_build_parse_prompt(email_text)` so both providers share it). Set `extracted_by="groq"` in the returned dict.
- Extend `parse_job_email(email_text, provider="local", gemini_model=None, groq_model=None)`:
  - When `provider == "groq"`: call `groq_parse_job_email_with_error()`, fall back to `local_parse_job_email()` on failure, populate `provider_used`, `provider_error`, and a new `groq_model` field. Mirror the existing Gemini branch.
  - Always include both `gemini_model` and `groq_model` keys in the returned dict (use `None` for the inactive one) to keep downstream callers stable.
- Extend `parse_job_email_strict(email_text, provider="gemini", gemini_model=None, groq_model=None)`:
  - Branch on `provider`. Strict means **no local fallback** — return `(None, error)` on provider failure so Gmail sync can pause the email and retry later (existing behavior).

**`app/gmail.py`**

- Update imports to include `DEFAULT_GROQ_MODEL`, `DEFAULT_PARSER_PROVIDER` from `app.config`.
- Replace the single `gemini_model = os.getenv(...)` reads at lines 246 and 299 with reads of three values:
  ```python
  parser_provider = (os.getenv("PARSER_PROVIDER") or DEFAULT_PARSER_PROVIDER).strip().lower()
  gemini_model = (os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()
  groq_model = (os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL).strip()
  ```
- Change `_parse_pending_emails` signature (line 313) to:
  ```python
  def _parse_pending_emails(connection, *, parser_provider, gemini_model, groq_model):
  ```
  Inside, pass `provider=parser_provider`, `gemini_model=gemini_model`, `groq_model=groq_model` into `parse_job_email_strict(...)`.
- Change `sync_gmail_messages(app)` to `sync_gmail_messages(app, *, parser_provider=None, gemini_model=None, groq_model=None)`. When the kwargs are `None`, fall back to env vars (existing behavior). When provided (from the route's form), they win — that is how the dropdown selection takes effect for the current sync.
- Same treatment for `retry_parse_email` (line 283) — accept the same optional kwargs and forward them.

**`app/routes.py`** (line 302, `gmail_sync_route`)

- Read `request.form.get("parser_choice", "")`. If non-empty and present in `PARSER_MODEL_CHOICES`, split on `:` to derive provider + model; pass them into `sync_gmail_messages(app, parser_provider=..., gemini_model=... if provider == "gemini" else None, groq_model=... if provider == "groq" else None)`.
- In the dashboard route that renders `index.html` (find the `@app.get("/")` or equivalent that builds the template context), add to the context dict:
  ```python
  "parser_choices": [(value, label) for (value, label, _p, _m) in PARSER_MODEL_CHOICES],
  "current_parser_choice": DEFAULT_PARSER_CHOICE,
  ```

### 2. Layout redesign — sidebar left, table right

**`templates/index.html`** — restructure the inside of `<main class="shell">`:

- Add class `shell-split` to the main element.
- Wrap the existing **hero** (lines 20–30), **gmail-card** (lines 49–80), and **stats-grid** (lines 42–47) in a new `<aside class="sidebar">` block, in that order: hero → gmail-card → stats-grid.
- Wrap the **top-note** (line 16), **flash-stack** (lines 32–40), and **tabs-container + everything below** (line 83 onward, including the dashboard tab and emails tab content) in a new `<section class="main-column">` block.
- Inside `gmail-card-actions` (lines 67–78), restructure so the parser dropdown is part of the **same form** as Sync now (so the selection submits with the click):
  ```html
  {% if gmail_status.connected %}
  <form method="post" action="{{ url_for('gmail_sync_route') }}" class="sync-form">
    <select name="parser_choice" class="parser-select">
      {% for value, label in parser_choices %}
        <option value="{{ value }}" {% if value == current_parser_choice %}selected{% endif %}>{{ label }}</option>
      {% endfor %}
    </select>
    <button class="button" type="submit">Sync now</button>
  </form>
  <form method="post" action="{{ url_for('gmail_disconnect_route') }}">
    <button class="button secondary" type="submit">Disconnect</button>
  </form>
  {% else %}
  <a class="button" href="{{ url_for('gmail_connect_route') }}">Connect Gmail</a>
  {% endif %}
  ```

**`static/styles.css`**

- Add new rules (place near `.shell` at line 49):
  ```css
  .shell-split {
    display: grid;
    grid-template-columns: 320px minmax(0, 1fr);
    gap: 24px;
    align-items: start;
  }
  .sidebar {
    display: flex;
    flex-direction: column;
    gap: 16px;
    position: sticky;
    top: 24px;
  }
  .main-column { min-width: 0; }
  ```
- Override the existing side-by-side `gmail-card` flex layout (line 231) when inside the sidebar so it stacks vertically:
  ```css
  .sidebar .gmail-card { flex-direction: column; }
  .sidebar .gmail-card-copy,
  .sidebar .gmail-card-status { max-width: none; min-width: 0; }
  ```
- Override `stats-grid` columns when inside the sidebar (current rule line 223 uses 4 columns):
  ```css
  .sidebar .stats-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  ```
- Style the parser dropdown next to the button:
  ```css
  .sync-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .parser-select {
    height: 38px;
    padding: 0 10px;
    border-radius: 8px;
    border: 1px solid var(--border, #d6d6d6);
    background: #fff;
    font: inherit;
  }
  ```
  (Match the variable name actually used in `styles.css` for borders; if there isn't one, hardcode the color used elsewhere on `.button.secondary`.)
- Mobile breakpoint — extend the existing `@media` block at styles.css:1160:
  ```css
  @media (max-width: 900px) {
    .shell-split { grid-template-columns: 1fr; }
    .sidebar { position: static; }
  }
  ```

### 3. Status badge one-line fix

**`static/styles.css`** at the `.badge` rule (lines 569–576):

- Add `white-space: nowrap;`. That single property fixes the "Interview Scheduled" wrap. No template change needed.

---

## Critical files

| File | Purpose |
|------|---------|
| `app/config.py` | Add Groq + provider constants and `PARSER_MODEL_CHOICES` |
| `app/email_parser.py` | Add Groq parser fn, share prompt builder, extend dispatchers |
| `app/gmail.py` | Wire provider/model selection through sync pipeline |
| `app/routes.py` | Read `parser_choice` form field; pass choices to template context |
| `templates/index.html` | Sidebar + main-column structure; parser dropdown in sync form |
| `static/styles.css` | `.shell-split` grid, sidebar overrides, `.parser-select`, badge `nowrap` |

## Reused functions / patterns
- `normalize_gemini_model()` pattern → mirror as `normalize_groq_model()`.
- `gemini_parse_job_email_with_error()` shape and return tuple → mirror exactly so downstream `app/parsed_emails.py` needs no changes.
- Existing fallback-to-local behavior in `parse_job_email()` → reused for the Groq path.
- Existing flash-message + redirect pattern in `gmail_sync_route` → unchanged.

## Out of scope (don't do these now)
- Persisting parser choice across sessions in the DB. The form field carries the choice per-sync; the dropdown shows `DEFAULT_PARSER_CHOICE` on initial load.
- Reparsing previously parsed emails with the new provider. Only newly synced or retried emails use the new selection.
- Any changes to the local heuristic parser (`local_parse_job_email`).

---

## Verification

1. **Install + run**
   - `uv sync` to pick up the `groq` dep already in `pyproject.toml`.
   - `uv run job-tracker` (or `python main.py`) → open `http://127.0.0.1:3000`.

2. **Visual / layout**
   - Confirm sidebar (title hero, Gmail card, 2×2 stats) on the left; applications table fills the right column at full page height.
   - Resize browser narrow (<900px) → sidebar should stack above the table.
   - Confirm an "Interview Scheduled" row renders the badge on a single line. Cross-check with other long statuses ("Final Interview", "Offer Received") — also one line.

3. **Groq parsing**
   - Select **"Groq · Llama 3.3 70B Versatile"** in the dropdown, click **Sync now** with Gmail connected.
   - Verify a freshly parsed job email shows `provider_used="groq"` in the Emails tab detail view (rendered by `app/parsed_emails.py`).
   - Repeat with **"Groq · Llama 3.1 8B Instant"**.
   - Repeat with each Gemini option to confirm regression-free.

4. **Failure paths**
   - Temporarily blank `GROQ_API_KEY` in env, pick a Groq option, click Sync → email lands in the paused queue with error string `Groq request failed because GROQ_API_KEY was not set.` (mirroring the Gemini error wording).
   - Restore the key after testing.

5. **Tests**
   - `uv run pytest` — existing tests should still pass.
   - Add a unit test in `tests/` for `groq_parse_job_email_with_error()` happy-path with a mocked Groq client (mirror any existing Gemini test pattern in `tests/`).
