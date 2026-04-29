# Product Requirements Document
## Job Application Tracker
**Version:** 1.1  
**Status:** Active Development  
**Author:** Personal Project  
**Last Updated:** April 29, 2026

---

## 1. Overview

### 1.1 Problem Statement
Job hunting is chaotic. Applications scatter across LinkedIn, Indeed, Prosple, and company websites. Without a system, it's easy to miss follow-ups, forget where you applied, and lose track of where you stand — especially when ghosting is the norm.

### 1.2 Goal
Build a personal job application tracker that keeps everything in one place, auto-captures applications via Gmail and a Chrome extension, and sends reminders so no opportunity slips through the cracks.

### 1.3 Core Value Proposition
> "Never lose track of a job application again — from first click to offer."

---

## 2. Users

**Primary User:** The builder (you) — an intermediate-technical job seeker applying across multiple platforms who wants visibility into their pipeline without manual data entry overhead.

---

## 3. Scope — V1

### In Scope
- Job application CRUD (Create, Read, Update, Delete)
- Gmail integration with AI-powered email parsing
- Chrome extension for one-click capture from job sites
- Status tracking with custom statuses
- Notes per application
- Salary/compensation tracking
- Follow-up reminders and alerts
- Simple table/list view dashboard
- Local backend (runs on your machine)

### Out of Scope (V1)
- Mobile app
- Multi-user / team features
- Cloud hosting / deployment
- Calendar integrations
- Auto-apply functionality
- Resume tracking per application

---

## 4. Features & Requirements

### 4.1 Application Data Model

Each job application record stores:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | Yes | Auto-generated |
| `company` | String | Yes | Company name |
| `role` | String | Yes | Job title |
| `job_url` | URL | No | Link to posting |
| `source` | Enum | No | LinkedIn, Indeed, Prosple, Direct, Other |
| `status` | Enum | Yes | See 4.2 |
| `applied_date` | Date | Yes | When application was submitted |
| `salary_min` | Number | No | Compensation range low |
| `salary_max` | Number | No | Compensation range high |
| `salary_currency` | String | No | Default: PHP |
| `notes` | Text | No | Free-form notes |
| `follow_up_date` | Date | No | Reminder date |
| `source_type` | Enum | No | gmail, chrome_extension, manual |
| `created_at` | Timestamp | Yes | Auto |
| `updated_at` | Timestamp | Yes | Auto |

### 4.2 Application Statuses

Statuses follow the real-world hiring funnel:

```
Applied → Interview Scheduled → Technical Test → Final Interview → Offer Received
                                                                 ↘ Rejected
                                                                 ↘ Ghosted
```

| Status | Description |
|---|---|
| `Applied` | Submitted application, awaiting response |
| `Interview Scheduled` | First interview confirmed |
| `Technical Test` | Take-home or timed coding/skills assessment |
| `Final Interview` | Late-stage interview round |
| `Offer Received` | Received an offer |
| `Rejected` | Formal rejection received |
| `Ghosted` | No response after reasonable time |

### 4.3 Gmail Auto-Detection (AI Parsing)

**Goal:** Automatically detect job-related emails and create or update application records without manual input.

**How it works:**
1. User grants OAuth 2.0 access to Gmail (read-only scope)
2. Local backend polls Gmail API at a configurable interval (default: every 15 minutes)
3. New emails are passed to an AI parser through a model-agnostic adapter (supports Google Gemini, OpenAI, or self-hosted/open-source LLMs)
4. AI extracts: company name, role, status signal, interview date if present
5. System matches to existing record (by company + role fuzzy match) or creates/updates record (deduped via `gmailMessageId`)
6. User is notified of new/updated records in the dashboard

**Email types to detect:**
- Application confirmation ("Thanks for applying to [Role] at [Company]")
- Interview invitations (contains date/time/calendar link)
- Rejection emails ("We've decided to move forward with other candidates")
- Offer letters
- Technical test invitations

**AI model options (recommended):**
- **Google Gemini (Vertex AI)** — cloud-hosted, high-quality understanding (recommended if you have a Google Cloud account)
- **OpenAI** — alternative hosted option
- **Open-source / self-hosted** — Llama 2, Mistral, or other models served locally or via Hugging Face Inference; good free/low-cost fallback for personal use

The system uses a small adapter so switching providers is simple and configurable via environment variables (`AI_PROVIDER=gemini|openai|local`).

**AI Parsing Prompt Contract (model-agnostic):**
The parsing adapter expects the LLM to return strict JSON matching this schema:
```json
{
  "company": "string|null",
  "role": "string|null",
  "status": "Applied|Interview Scheduled|Technical Test|Final Interview|Offer Received|Rejected|Ghosted|null",
  "interview_date": "ISO8601|null",
  "confidence": 0.0,
  "is_job_related": true|false
}
```

**Acceptance Criteria:**
- Correctly identifies job emails vs. non-job emails with >90% accuracy (using a configurable confidence threshold)
- Extracts company and role from confirmation emails reliably
- Maps email tone/content to correct status and interview dates when present
- Does not duplicate records for the same job (dedupe by `gmailMessageId` and fuzzy match)
- Gracefully handles parsing failures (logs error, flags email for manual review)

**Notes on costs and free options:**
- If you prefer not to use hosted APIs, run an open-source model locally (Llama 2, Mistral) or use Hugging Face Inference free tiers. For local use, a lightweight on-device model or a small containerized inference server is sufficient for structured extraction tasks.

### 4.4 Chrome Extension

**Goal:** Let the user capture a job application in one click while browsing job sites.

**Supported Sites (V1):**
- LinkedIn (`linkedin.com/jobs`)
- Indeed (`indeed.com`)
- Prosple (`prosple.com`)
- Any company career page (manual fallback with pre-filled URL)

**How it works:**
1. User navigates to a job posting
2. Clicks the extension icon
3. Extension scrapes: job title, company name, job URL from the page DOM
4. A small popup appears showing the pre-filled data
5. User can adjust fields, add notes, then click "Save"
6. Extension sends a `POST` request to the local backend API (`localhost:3000/api/applications`)
7. Success notification shown in popup

**Extension Popup Fields:**
- Company (pre-filled, editable)
- Role/Title (pre-filled, editable)
- Job URL (auto-filled)
- Source (auto-detected from domain)
- Status (default: Applied)
- Notes (optional)
- Salary range (optional)

**Acceptance Criteria:**
- Works on all 4 supported sites
- Correctly pre-fills company and role in >80% of cases
- Sends data to local backend within 2 seconds
- Shows clear success/failure feedback
- Does not break page functionality

### 4.5 Dashboard — Table/List View

**Goal:** A clean, scannable overview of all applications.

**Columns displayed:**
- Company
- Role
- Status (color-coded badge)
- Applied Date
- Source
- Follow-up Date (highlighted if overdue)
- Actions (Edit, Delete)

**Filtering & Sorting:**
- Filter by status
- Filter by source
- Sort by applied date, company name, status
- Search by company or role name

**Status badge colors:**
| Status | Color |
|---|---|
| Applied | Blue |
| Interview Scheduled | Yellow |
| Technical Test | Orange |
| Final Interview | Purple |
| Offer Received | Green |
| Rejected | Red |
| Ghosted | Gray |

### 4.6 Reminders & Follow-up Alerts

- User can set a `follow_up_date` per application
- Dashboard highlights overdue follow-ups in the table
- Optional: desktop notification via OS notification API when follow-up date arrives (local server-side cron)

### 4.7 Notes per Application

- Free-form text field per record
- Supports multi-line input
- Shown in application detail/edit view

### 4.8 Salary / Compensation Tracking

- Optional min/max salary range
- Currency field (default: PHP)
- Shown as a column in the table (optional, can be hidden)
- Not required for record creation

---

## 5. Technical Requirements

### 5.1 Architecture Overview

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Chrome Ext     │────▶│  Local Backend   │────▶│  SQLite DB  │
│  (Vanilla JS)   │     │  (Node/Express)  │     │             │
└─────────────────┘     └────────┬─────────┘     └─────────────┘
                                 │
              ┌──────────────────┼───────────────────┐
              ▼                  ▼                   ▼
        ┌──────────┐     ┌──────────────┐    ┌─────────────┐
        │ Gmail API│     │ Claude API   │    │  Frontend   │
        │ (OAuth)  │     │ (AI parsing) │    │  (React)    │
        └──────────┘     └──────────────┘    └─────────────┘
```

### 5.2 Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| Backend | Node.js + Express | Lightweight, easy local setup |
| Database | SQLite (via Prisma) | Zero-config, file-based, perfect for local |
| Frontend | React + Vite | Fast dev, easy table rendering |
| Chrome Extension | Manifest V3, Vanilla JS | Chrome standard |
| AI Parsing | Anthropic Claude API | Best-in-class email parsing |
| Gmail | Google OAuth 2.0 + Gmail API | Official, read-only scope |
| Auth (Gmail) | `googleapis` npm package | Simplifies OAuth flow |

### 5.3 Local Setup Requirements

- Node.js v20+
- A Google Cloud project with Gmail API enabled
- An Anthropic API key
- Chrome browser (for extension)
- Port 3000 available locally

### 5.4 API Endpoints (Backend)

```
GET    /api/applications          - List all applications (with filters)
POST   /api/applications          - Create new application
GET    /api/applications/:id      - Get single application
PUT    /api/applications/:id      - Update application
DELETE /api/applications/:id      - Delete application

POST   /api/gmail/auth            - Initiate Gmail OAuth flow
GET    /api/gmail/callback        - OAuth callback handler
POST   /api/gmail/sync            - Manually trigger Gmail sync
GET    /api/gmail/status          - Check OAuth connection status
```

---

## 6. Non-Functional Requirements

- **Performance:** Dashboard loads in <1 second with up to 500 applications
- **Reliability:** Gmail polling failures should not crash the server; log and retry
- **Privacy:** All data stays local; no telemetry, no cloud sync
- **Security:** `.env` stores API keys; never committed to git
- **Usability:** Setup should be completable in under 30 minutes by an intermediate developer

---

## 7. Out of Scope / Future Considerations (V2+)

- Cloud sync / backup
- Mobile view / PWA
- Multi-user support
- Email sending (automated follow-up drafts)
- Resume version tracking per application
- Interview prep notes with AI assistance
- Job search analytics / charts

---

## 8. Success Metrics (Personal)

- All active applications visible in one view
- Zero applications "lost" or forgotten
- Follow-up dates consistently set and acted on
- Gmail sync reduces manual entry by >70%

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Gmail OAuth setup complexity | Provide a step-by-step setup guide in README |
| AI parsing misidentifies emails | Confidence threshold filter; manual override always available |
| Chrome extension breaks on site DOM changes | Fallback to URL + manual fill; easy to patch selectors |
| SQLite data loss | Simple export-to-CSV button in V1 |