# Design Document
## Job Application Tracker
**Version:** 2.0  
**Status:** Active  
**Last Updated:** April 29, 2026  
**Tech Stack:** Flask (Python) + SQLite + Modular Architecture

---

## 1. System Architecture

### 1.1 High-Level Overview

The system is a locally-run Flask web app with three entry points for data:

```
┌──────────────────────────────────────────────────────────────┐
│                        DATA ENTRY POINTS                     │
│                                                              │
│   [Chrome Extension]   [Gmail Auto-Sync]   [Manual UI Form] │
│         │                    │                    │          │
└─────────┼────────────────────┼────────────────────┼──────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│              LOCAL BACKEND (Flask + Python)                  │
│                  localhost:3000                             │
│                                                             │
│   ┌──────────────┐   ┌──────────────┐  ┌───────────────┐  │
│   │  REST API    │   │  Email Parser│  │  Gemini / AI  │  │
│   │  /api/*      │   │  (Heuristic) │  │  (Optional)   │  │
│   └──────┬───────┘   └──────┬───────┘  └───────┬───────┘  │
│          └──────────────────┴──────────────────-┘          │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │  SQLite DB ORM  │                      │
│                    │  (sqlite3)      │                      │
│                    └────────┬────────┘                      │
└─────────────────────────────┼───────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   SQLite DB       │
                    │   (job_tracker.db)│
                    └───────────────────┘
                              ▲
                              │ reads
                    ┌─────────┴─────────┐
                    │ Jinja2 Templates  │
                    │  (Dashboard UI)   │
                    │  localhost:3000   │
                    └───────────────────┘
```

### 1.2 Component Responsibilities

| Component | Tech | Responsibility |
|---|---|---|
| **Web UI** | Jinja2 Templates + HTML/CSS | Dashboard, forms, application table |
| **Flask Backend** | Flask + Python | HTTP routing, request handling |
| **Database Layer** | sqlite3 native module | CRUD operations, indexing |
| **Email Parser** | Local heuristics + Gemini API (optional) | Extracts job data from raw emails |
| **Chrome Extension** | Manifest V3, Vanilla JS | DOM scraping, API calls to backend |

---

## 2. Database Design

### 2.1 Schema (SQLite)

```sql
CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    job_url TEXT,
    source TEXT,  -- "LinkedIn" | "Indeed" | "Prosple" | "Direct" | "Other"
    status TEXT NOT NULL,
    applied_date TEXT NOT NULL,  -- ISO format date
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,  -- Default: "PHP"
    notes TEXT,
    follow_up_date TEXT,
    source_type TEXT,  -- "gmail" | "chrome_extension" | "manual"
    created_at TEXT NOT NULL,  -- UTC ISO 8601 timestamp
    updated_at TEXT NOT NULL   -- UTC ISO 8601 timestamp
);

-- Indexes for fast filtering and sorting
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_company ON applications(company);
CREATE INDEX idx_applications_applied_date ON applications(applied_date);
```

### 2.2 Indexes

- `idx_applications_status` - Fast filtering by job application status
- `idx_applications_company` - Fast filtering by company name
- `idx_applications_applied_date` - Fast sorting and time-based queries

---

## 3. Backend Design

### 3.1 Modular Architecture

The backend is organized into focused, independent modules:

```
app/
├── __init__.py              # Flask app factory, initialization
├── config.py                # Constants (statuses, sources, demo data)
├── database.py              # Database connection and schema management
├── models.py                # CRUD operations (create, read, update, delete)
├── email_parser.py          # Email parsing (Gemini + local heuristics)
├── validators.py            # Data validation and normalization
├── utils.py                 # Helper functions (dates, strings, floats)
└── routes.py                # Flask route handlers (web + API)

main.py                       # Application entry point
```

### 3.2 Module Responsibilities

#### `app/__init__.py` - Application Factory
- `create_app(test_config)` - Creates and configures Flask instance
- `seed_demo_data(app)` - Populates database with sample data
- Environment file loading

#### `app/config.py` - Configuration & Constants
- Status options and enum values
- Source options (LinkedIn, Indeed, etc.)
- CSS styling mappings
- Demo data for seeding

#### `app/database.py` - Database Management
- `connect_db(app)` - Creates SQLite connections
- `init_db(app)` - Initializes schema (idempotent)
- Connection pooling (via sqlite3)

#### `app/models.py` - CRUD Operations
- `insert_application(connection, payload, timestamp)` - Create
- `fetch_applications(app, filters)` - List with filtering and sorting
- `fetch_application(app, id)` - Get single record
- `update_application(connection, id, payload, partial)` - Update
- `delete_application(app, id)` - Delete
- `serialize_application(row)` - Convert DB row to dict (adds computed fields)

#### `app/email_parser.py` - Email Parsing
- `parse_job_email(email_text, provider)` - Main entry point
- `gemini_parse_job_email_with_error(email_text)` - AI-powered parsing
- `local_parse_job_email(email_text)` - Rule-based heuristic parsing
- `detect_status()` - Infer status from email keywords
- `extract_company()` - Extract company name via regex
- `extract_role()` - Extract job title via regex
- `extract_date()` - Parse interview date (ISO, slash, month name formats)
- `looks_job_related()` - Quick keyword check for job-related emails

#### `app/validators.py` - Data Validation
- `normalize_payload(payload, partial)` - Clean and validate application data
- `form_payload(form)` - Convert Flask form data to normalized dict
- Ensures required fields, valid status enums, proper types

#### `app/utils.py` - Helper Functions
- `utc_now()` - Current UTC timestamp in ISO format
- `clean_string()` - Trim and validate strings
- `clean_company()` - Clean company name (remove extra whitespace)
- `to_float()` - Safe float conversion
- `parse_date()` - Parse ISO date strings

#### `app/routes.py` - HTTP Route Handlers
**Web Routes:**
- `GET /` - Dashboard with filters, stats, application list
- `POST /applications` - Create from form submission
- `POST /applications/<id>/update` - Update from form
- `POST /applications/<id>/delete` - Delete application
- `POST /parse-email` - Parse email and display results

**API Routes (JSON):**
- `GET /api/health` - Health check
- `GET /api/applications` - List (supports filters: status, source, search)
- `POST /api/applications` - Create
- `GET /api/applications/<id>` - Get single
- `PUT /api/applications/<id>` - Update (partial)
- `DELETE /api/applications/<id>` - Delete

### 3.3 Data Flow

#### Creating an Application (Web Form)
```
1. User fills form → POST /applications
2. routes.create_application_route()
   ├─ form_payload(request.form)
   │  └─ validators.form_payload() → normalize_payload()
   ├─ models.insert_application(connection, normalized, timestamp)
   │  └─ database.connect_db() → INSERT SQL
   └─ Redirect to dashboard
```

#### Creating an Application (API)
```
1. Client sends JSON → POST /api/applications
2. routes.api_create_application()
   ├─ normalize_payload(request.get_json())
   ├─ models.insert_application()
   └─ Return JSON 201
```

#### Parsing Email
```
1. User submits email text + provider choice
2. routes.parse_email_route()
   ├─ email_parser.parse_job_email(email_text, provider)
   │  ├─ If provider='gemini':
   │  │  ├─ gemini_parse_job_email_with_error(email)
   │  │  │  └─ Call Gemini API (google.genai.Client)
   │  │  ├─ If fails, fallback to local parser
   │  │
   │  └─ If provider='local' (default):
   │     └─ local_parse_job_email(email)
   │        ├─ looks_job_related()
   │        ├─ extract_company()
   │        ├─ extract_role()
   │        ├─ detect_status()
   │        ├─ extract_date()
   │        └─ Return parsed result
   └─ Render results in template
```

### 3.4 Email Parsing Strategy (Dual Mode)

**Local Parser (Default)**
- Uses keyword matching and regex patterns
- No API calls, no latency
- Confidence score 0.12 - 0.98 based on matched fields
- Keywords for status detection: offer, final, technical, interview, rejected, regret
- Regex patterns for company, role, date extraction

**Gemini Parser (Optional)**
- Calls Google Gemini API (if `GEMINI_API_KEY` or `GOOGLE_API_KEY` set)
- Returns structured JSON with reasoning and field explanations
- Fallback: automatically uses local parser if API fails or key missing
- Provider configurable via form dropdown or env variable

---

## 4. Chrome Extension Design

### 4.1 Project Structure

```
extension/
├── manifest.json             # Manifest v3 config
├── popup/
│   ├── popup.html            # Popup UI
│   ├── popup.js              # Popup logic
│   └── popup.css             # Popup styles
├── content_scripts/
│   └── scraper.js            # DOM scraping on job sites
├── background/
│   └── service_worker.js     # Message handling
└── icons/
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```

### 4.2 Manifest V3

```json
{
  "manifest_version": 3,
  "name": "Job Tracker",
  "version": "1.0.0",
  "description": "Capture job applications in one click",
  "permissions": ["activeTab", "scripting"],
  "host_permissions": [
    "https://www.linkedin.com/*",
    "https://www.indeed.com/*",
    "https://prosple.com/*",
    "http://localhost:3000/*"
  ],
  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": { "48": "icons/icon48.png" }
  }
}
```

---

## 5. Testing

### 5.1 Test Coverage
- Unit tests for email parsing (local + Gemini)
- Integration tests for API endpoints
- Database operation tests
- Validator/normalization tests

### 5.2 Running Tests
```bash
uv run python -m pytest tests/ -v
```

---

## 6. Configuration

### 6.1 Environment Variables (`.env`)

```bash
# Flask
SECRET_KEY=dev                    # Development secret key
DATABASE_PATH=./job_tracker.db    # SQLite DB location
SEED_DEMO_DATA=true               # Load demo data on startup
TESTING=false                     # Test mode

# AI Provider
AI_PROVIDER=local                 # "local" (default) | "gemini"
GEMINI_API_KEY=<your-key>        # Optional for Gemini parsing
GOOGLE_API_KEY=<your-key>        # Alternative to GEMINI_API_KEY
```

---

## 7. Development Workflow

### 7.1 Setup
```bash
cd job-tracker
uv sync
uv run python main.py
```

### 7.2 Adding a New Feature
1. Create module in `app/` (e.g., `app/candidates.py`)
2. Implement logic
3. Import and use in `app/routes.py`
4. Add tests in `tests/`
5. Commit with clear message

### 7.3 Deployment
Currently runs locally. For cloud deployment:
- Replace SQLite with PostgreSQL
- Add WSGI server (Gunicorn)
- Containerize with Docker
- Deploy to cloud (Heroku, AWS, GCP)
  }],
  "background": {
    "service_worker": "background/service_worker.js"
  }
}
```

### 4.3 DOM Scraping Rules

```javascript
// scraper.js — site-specific selectors

const SCRAPERS = {
  "linkedin.com": {
    company: () => document.querySelector(".job-details-jobs-unified-top-card__company-name a")?.innerText.trim(),
    role:    () => document.querySelector(".job-details-jobs-unified-top-card__job-title h1")?.innerText.trim(),
    source:  () => "LinkedIn"
  },
  "indeed.com": {
    company: () => document.querySelector('[data-testid="inlineHeader-companyName"]')?.innerText.trim(),
    role:    () => document.querySelector('[data-testid="jobsearch-JobInfoHeader-title"]')?.innerText.trim(),
    source:  () => "Indeed"
  },
  "prosple.com": {
    company: () => document.querySelector(".employer-name")?.innerText.trim(),
    role:    () => document.querySelector("h1.job-title")?.innerText.trim(),
    source:  () => "Prosple"
  }
};
```

### 4.4 Extension ↔ Backend Communication

```
User clicks extension icon
       │
       ▼
popup.js sends message to content_scripts/scraper.js
       │
       ▼
scraper.js returns { company, role, url, source }
       │
       ▼
popup.html pre-fills the form
       │
User adjusts + clicks "Save"
       │
       ▼
popup.js → POST http://localhost:3000/api/applications
       │
       ▼
Shows ✅ success or ❌ error in popup
```

---

## 5. Frontend Design

### 5.1 Project Structure

```
frontend/
├── src/
│   ├── App.tsx
│   ├── components/
│   │   ├── ApplicationTable.tsx    # Main table view
│   │   ├── ApplicationRow.tsx      # Single row
│   │   ├── StatusBadge.tsx         # Color-coded status badge
│   │   ├── AddApplicationModal.tsx # New application form
│   │   ├── EditApplicationModal.tsx
│   │   ├── FilterBar.tsx           # Status/source filters + search
│   │   └── GmailStatus.tsx         # Gmail connection status widget
│   ├── hooks/
│   │   ├── useApplications.ts      # Fetch + CRUD logic
│   │   └── useGmailStatus.ts
│   ├── api/
│   │   └── client.ts               # Axios/fetch wrapper
│   └── types/
│       └── index.ts
└── vite.config.ts
```

### 5.2 Page Layout

```
┌───────────────────────────────────────────────────────────┐
│  📋 Job Tracker                          [+ Add Job]      │
│  ─────────────────────────────────────────────────────    │
│  Gmail: ✅ Connected · Last sync: 2 min ago  [Sync Now]   │
│                                                           │
│  [All Statuses ▾]  [All Sources ▾]  [🔍 Search...]       │
│                                                           │
│  Company      Role          Status       Applied   Action │
│  ─────────────────────────────────────────────────────── │
│  Canva        UX Designer   🟡 Interview  Apr 20    ✏️ 🗑️ │
│  Atlassian    SWE Intern    🔵 Applied    Apr 18    ✏️ 🗑️ │
│  Figma        PM            🔴 Rejected   Apr 10    ✏️ 🗑️ │
│  Shopify      Dev Advocate  ⚫ Ghosted    Apr 5     ✏️ 🗑️ │
│                                                           │
│  Showing 4 of 23 applications                            │
└───────────────────────────────────────────────────────────┘
```

### 5.3 Status Badge Color Map

```typescript
const STATUS_STYLES = {
  "Applied":             "bg-blue-100 text-blue-800",
  "Interview Scheduled": "bg-yellow-100 text-yellow-800",
  "Technical Test":      "bg-orange-100 text-orange-800",
  "Final Interview":     "bg-purple-100 text-purple-800",
  "Offer Received":      "bg-green-100 text-green-800",
  "Rejected":            "bg-red-100 text-red-800",
  "Ghosted":             "bg-gray-100 text-gray-600",
};
```

---

## 6. Gmail OAuth Setup Flow

### 6.1 One-Time Setup (Developer Steps)

```
1. Go to console.cloud.google.com
2. Create project: "Job Tracker"
3. Enable Gmail API
4. Create OAuth 2.0 credentials (Desktop / Web App)
5. Add authorized redirect URI: http://localhost:3000/api/gmail/callback
6. Copy Client ID and Client Secret → add to .env

GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:3000/api/gmail/callback
AI_PROVIDER=local          # gemini|openai|local
PROVIDER_API_KEY=your-provider-key  # optional, used by hosted providers
PORT=3000
```

### 6.2 User Auth Flow (In-App)

```
Dashboard → Click "Connect Gmail"
    │
    ▼
Backend redirects to Google OAuth consent
    │
    ▼
User approves gmail.readonly scope
    │
    ▼
Google redirects to /api/gmail/callback
    │
    ▼
Backend stores tokens → Dashboard shows ✅ Connected
    │
    ▼
Polling starts automatically
```

---

## 7. Security Considerations

- Gmail OAuth uses `gmail.readonly` scope only — the app can never send or delete emails
- API keys stored in `.env`, which is `.gitignored`
- No data leaves your machine (Claude API receives only email text snippets, not full mailbox)
- Local SQLite file is the only data store — easy to inspect, backup, or delete
- Chrome extension only has `host_permissions` for specific job sites + localhost

---

## 8. Dev Setup & Local Run

```bash
# Clone and install
git clone <your-repo>
cd job-tracker

# Backend
cd backend
cp .env.example .env       # Fill in API keys
npm install
npx prisma migrate dev
npm run dev                 # Starts on localhost:3000

# Frontend (new terminal)
cd frontend
npm install
npm run dev                 # Starts on localhost:5173

# Chrome Extension
# 1. Open chrome://extensions
# 2. Enable "Developer mode"
# 3. Click "Load unpacked" → select /extension folder
# 4. Pin the extension to your toolbar
```

---

## 9. Key Design Decisions

### Why SQLite?
Zero setup, single file, no DB server to run. For a personal tracker with <10k records, it's more than fast enough. Easy to back up — just copy `tracker.db`.

### Why Claude for parsing?
Rule-based email parsing is brittle. Job emails vary wildly in format. Claude handles ambiguity, extracts company/role from unusual formats, and maps tone to the correct status — far more reliably than regex.

### Why a local backend vs. serverless?
Gmail OAuth requires a server to hold refresh tokens securely. Running locally keeps everything private, avoids hosting costs, and aligns with the constraint that everything stays on your machine.

### Why Manifest V3 for the extension?
MV3 is the current Chrome standard. MV2 extensions are being phased out. Building on MV3 ensures longevity.

### Why not a browser storage-only extension?
A backend is needed anyway for Gmail polling. Having the extension POST to the same backend keeps data in one place (SQLite) rather than split between extension storage and a backend DB.