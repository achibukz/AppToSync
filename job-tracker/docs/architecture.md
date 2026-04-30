# Project Refactoring: Modular Architecture

## Overview
The Job Tracker application has been successfully refactored from a monolithic `app.py` structure into a modular, feature-based architecture. This makes the codebase more maintainable, testable, and easier to debug.

## New Directory Structure

```
job-tracker/
├── main.py                 # Application entry point
├── app/                    # Main application package
│   ├── __init__.py        # App factory (create_app) and initialization
│   ├── config.py          # Constants and configuration
│   ├── database.py        # Database connection and initialization
│   ├── models.py          # CRUD operations for applications
│   ├── email_parser.py    # Email parsing (Gemini + local heuristics)
│   ├── validators.py      # Data validation and normalization
│   ├── utils.py           # Utility functions (string cleaning, date parsing)
│   └── routes.py          # Flask routes (web + API endpoints)
├── tests/                 # Test files (updated for new structure)
├── templates/             # HTML templates
├── static/                # CSS and static files
└── pyproject.toml         # Project configuration
```

## Module Breakdown

### `app/__init__.py`
**Purpose:** Flask application factory and initialization
- `create_app(test_config)` - Creates and configures the Flask app
- `seed_demo_data(app)` - Seeds the database with demo data
- Environment loading from `.env` file

### `app/config.py`
**Purpose:** Constants and configuration
- `STATUS_OPTIONS` - Available job application statuses
- `SOURCE_OPTIONS` - Where jobs came from (LinkedIn, Indeed, etc.)
- `SOURCE_TYPE_OPTIONS` - How applications were captured (gmail, extension, manual)
- `STATUS_STYLES` - CSS classes for status styling
- `MONTHS` - Month name to number mapping
- `DEMO_APPLICATIONS` - Sample data for seeding

### `app/database.py`
**Purpose:** Database connection and schema management
- `connect_db(app)` - Creates database connections
- `init_db(app)` - Initializes database schema with tables and indexes
- `get_db_path(app)` - Gets the database path from config

### `app/models.py`
**Purpose:** CRUD operations for job applications
- `insert_application()` - Create new applications
- `update_application()` - Update existing applications
- `delete_application()` - Remove applications
- `fetch_application()` - Get a single application
- `fetch_applications()` - List applications with filtering
- `serialize_application()` - Convert database rows to dictionaries

### `app/email_parser.py`
**Purpose:** Email parsing and job details extraction
- `parse_job_email()` - Main parsing function (supports Gemini + fallback to local)
- `gemini_parse_job_email()` - Parse using Gemini AI API
- `gemini_parse_job_email_with_error()` - Parse with error handling
- `local_parse_job_email()` - Parse using regex heuristics
- `detect_status()` - Extract job status from email
- `extract_company()` - Extract company name
- `extract_role()` - Extract job title/role
- `extract_date()` - Extract interview date (supports multiple formats)
- `looks_job_related()` - Determine if email is job-related

### `app/validators.py`
**Purpose:** Data validation and normalization
- `normalize_payload()` - Validate and clean application data
- `form_payload()` - Convert form data to normalized payload
- Ensures required fields, valid statuses, proper data types

### `app/utils.py`
**Purpose:** Utility and helper functions
- `utc_now()` - Get current UTC timestamp
- `clean_string()` - Clean and normalize strings
- `clean_company()` - Clean company names
- `to_float()` - Safe float conversion
- `parse_date()` - Parse ISO format dates

### `app/routes.py`
**Purpose:** Flask route handlers
- **Web Routes:**
  - `GET /` - Dashboard with filters and application list
  - `POST /applications` - Create application from form
  - `POST /applications/<id>/update` - Update application
  - `POST /applications/<id>/delete` - Delete application
  - `POST /parse-email` - Parse email and display results
  
- **API Routes:**
  - `GET /api/health` - Health check
  - `GET /api/applications` - List applications (JSON)
  - `POST /api/applications` - Create application (JSON)
  - `GET /api/applications/<id>` - Get single application
  - `PUT /api/applications/<id>` - Update application (JSON)
  - `DELETE /api/applications/<id>` - Delete application

## Benefits of the New Structure

1. **Separation of Concerns** - Each module has a single, well-defined responsibility
2. **Easier Debugging** - Find bugs faster by looking at specific modules
3. **Better Testing** - Each module can be tested independently
4. **Improved Maintainability** - Changes are localized to specific modules
5. **Code Reusability** - Modules can be easily imported and used elsewhere
6. **Scalability** - Easy to add new features by creating new modules

## Testing

All tests have been updated to import from the new modular structure:

```bash
# Run all tests
uv run python -m pytest tests/ -v

# Key test results:
# ✓ test_api_create_and_list_application
# ✓ test_api_delete_application
# ✓ test_gemini_parser_uses_api_response_shape
# ✓ test_local_parser_detects_interview_email
```

## Running the Application

```bash
# From the job-tracker directory
uv run python main.py

# The app will start at http://127.0.0.1:3000
```

## Example: Adding a New Feature

To add a new feature (e.g., candidate tracking), you would:

1. Create a new module: `app/candidates.py`
2. Implement your logic there
3. Import it in the relevant route handlers in `app/routes.py`
4. Create tests in `tests/test_candidates.py`

This modular approach keeps the code organized and makes it easy to find and modify specific functionality.

  1. The Google Gmail API Mechanism
  Your app interacts with Gmail using two main components: OAuth 2.0 for permission and the RESTful Gmail API for data.

   * OAuth 2.0 (Authorization):
       * The Flow: When you click "Connect Gmail," the app uses google-auth-oauthlib to generate a unique authorization URL. You log in
         via Google, and Google sends an Authorization Code back to your redirect URI (localhost:3000/gmail/callback).
       * Tokens: Your app exchanges this code for an Access Token (short-lived, used for API calls) and a Refresh Token (long-lived,
         used to get new access tokens without asking you to log in again).
       * Storage: These credentials are encrypted as JSON and stored in your SQLite gmail_connections table.

   * API Scopes: Your app requests https://www.googleapis.com/auth/gmail.readonly, which allows it to list and read emails but not send
     or delete them, ensuring user safety.

  ---

  2. The Data Pipeline: From Email to Table
  The pipeline is managed by app/gmail.py and runs in a background thread started in main.py.

  Step A: Discovery (Polling)
  The _gmail_poll_loop runs every 15 minutes (configurable). It checks the gmail_connections table to see if a sync is due.
   1. Query Generation: It calculates the timestamp of the last sync and builds a Gmail search query: in:anywhere after:{timestamp}.
   2. Listing: It calls users.messages().list() to get a list of Message IDs that match the query.

  Step B: Extraction (Raw Data)
  For each Message ID found:
   1. Fetching: The app calls users.messages().get() to download the full email metadata (headers) and body (parts).
   2. Decoding: Email bodies are typically Base64 encoded. The _message_to_text function extracts the Subject, From, Date, and the
      plain-text or HTML body (which it strips of tags).

  Step C: Intelligence (Parsing)
  The raw text is passed to app/email_parser.py:
   1. Validation: It first runs looks_job_related() to filter out newsletters or noise.
   2. Extraction:
       * If AI_PROVIDER=gemini, it sends the text to the Gemini API with a prompt to extract JSON containing company, role, status, and
         date.
       * If it fails or is set to local, it uses regex heuristics to find keywords like "Interview," "Applied," or "Unfortunately."

  Step D: Reconciliation (Database Update)
  This is where the app decides whether to add a new row or update an existing one:
   1. Fuzzy Matching: It searches the applications table for the gmail_message_id. If not found, it does a fuzzy search by company and
      role to see if you manually added the application earlier.
   2. Status Logic (_choose_status): If the email is a status update (e.g., you were "Applied" but now the email says "Interview"), it
      compares the "priority" of the statuses. It will only update the status if the new one is "further" in the hiring process (e.g.,
      it won't move you back to "Applied" if you are already "Interviewing").
   3. Note Merging: It appends the email subject and parsing notes to the existing notes field so you don't lose previous history.
   4. Final Write: It executes an INSERT or UPDATE in the SQLite database, marking the sync as successful.

  Summary Flowchart

   1 [Gmail Inbox] 
   2       ↓ (API: list & get)
   3 [Raw Email JSON] 
   4       ↓ (Base64 Decode + HTML Strip)
   5 [Clean Text] 
   6       ↓ (Gemini AI / Heuristics)
   7 [Structured JSON (Company, Role, Status)]
   8       ↓ (Fuzzy Match in Database)
   9 [Final Application Row in SQLite]

   Email Tab

     ---                                                                                                                             
  What changed                                                                          
                                                                                                                                  
  The Gmail sync pipeline is now fetch → store → parse → review instead of fetch → parse → save. Every email is stored locally
  first, then parsed via Gemini-only (no local-regex fallback), then either auto-routed to an existing app or queued for your     
  accept/reject.                         
                                                                                                                                  
  Files changed                                                                         
                                                                                                                                  
  ┌────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────┐  
  │              File              │                                           What                                            │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤  
  │ app/database.py                │ New parsed_emails table (Gmail message metadata + parser output + review state)           │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤  
  │ app/parsed_emails.py           │ New CRUD module: upsert, fetch by status, mark accepted/dismissed/retry                   │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤  
  │ app/email_parser.py            │ Added parse_job_email_strict — Gemini-only, returns (result, error)                       │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤  
  │                                │ Two-step sync: store every fetched message, then parse paused rows. Watcher OR fuzzy      │
  │ app/gmail.py                   │ match → silent auto-update; otherwise → pending_review. Gemini failure → stays paused,    │  
  │                                │ retried next sync. New retry_parse_email() for one-off manual retry                       │
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤  
  │ app/routes.py                  │ New /emails/<id>/accept, /emails/<id>/reject, /emails/<id>/retry + /api/emails. Removed   │
  │                                │ /parse-email. Dashboard now passes pending_emails and paused_emails to the template       │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
  │ app/validators.py              │ normalize_payload now preserves gmail_message_id so accepted-email apps link back to      │  
  │                                │ their source                                                                              │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
  │                                │ Replaced "Email Parser" tab with Emails tab (badge with pending count). Pending review    │  
  │ templates/index.html           │ cards show parsed company/role/status/confidence with Accept / Edit & Accept / Reject     │
  │                                │ buttons. Paused section with Retry. Edit & Accept opens the existing modal pre-filled     │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
  │ static/styles.css              │ Email card styles, tag pills, paused state, danger button                                 │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
  │ tests/test_parsed_emails.py    │ New: 27 tests across CRUD, sync pipeline (auto-update via watcher/fuzzy, pending review,  │  
  │                                │ not-job, paused, retry-on-next-sync, dedup), and accept/reject/retry routes               │
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤  
  │ tests/test_company_watchers.py │ Updated for new sync result shape (fetched/parsed/pending_review/paused/updated/not_job); │
  │                                │  fallback branch now expects pending_review instead of auto-create                        │  
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┤
  │ tests/test_gmail_sync.py       │ Updated to mock parse_job_email_strict and assert the new queue behavior                  │  
  └────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────┘  
                                                                       
  Behavior to verify in the UI: connect Gmail → run sync → check the Emails tab. New unmatched job emails appear in Pending       
  review. If Gemini quota is exhausted, emails appear in Paused with a Retry button.    
                                                                                       