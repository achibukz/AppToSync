# Tasks to Improve

Improvement opportunities identified via codebase analysis. Organized by severity.

## 🔴 High Severity

### 1. Hardcoded Credentials
**File:** `app/database.py:45`
**Issue:** Default owner password "JobPilot2026!" is embedded in source code.
**Fix:** Move to environment variables with a generated default during first setup.

### 2. Silent Background Failures
**File:** `app/gmail.py:408`
**Issue:** Background polling loop uses bare `except Exception: pass`, completely masking sync failures.
**Fix:** Add logging and consider a failure counter to alert or trigger retries.

### 3. Weak Session Validation
**File:** `app/auth.py:42`
**Issue:** `login_required` only checks if `user_id` exists in session, never verifies the user still exists in the database or handles session timeout.
**Fix:** Add a DB existence check and implement session timeout logic.

### 4. Information Leakage in Errors
**File:** `app/email_parser.py:118`
**Issue:** `gemini_parse_job_email_with_error` returns raw exception strings to callers; can leak stack traces or API details.
**Fix:** Return generic errors to client, log detailed errors server-side.

---

## 🟡 Medium Severity

### 5. O(N) Fuzzy Matching Performance
**File:** `app/models.py:90`
**Issue:** `find_fuzzy_application` fetches all records and does fuzzy matching in Python. Will not scale.
**Fix:** Index company names, use a trie/BK-tree structure, or limit to recent applications.

### 6. Full Table Scans Defeating Indexes
**File:** `app/models.py:202`
**Issue:** `fetch_applications` uses `ORDER BY ... COLLATE NOCASE` on columns indexed without that collation, defeating the index.
**Fix:** Either index with `COLLATE NOCASE` or use a separate sort column.

### 7. AI Parsing Blocks Sync Thread
**File:** `app/gmail.py:293`
**Issue:** `_parse_pending_emails` attempts to re-parse all paused emails during each sync. High-latency API calls block subsequent syncs.
**Fix:** Offload to a separate queue or task worker.

### 8. Synchronous I/O in Background Loop
**File:** `app/gmail.py:382`
**Issue:** Email decoding and HTML stripping happens inline during the sync loop.
**Fix:** Move to an async or offloaded step.

### 9. Missing Rate Limits on REST API
**File:** `app/routes.py:530`
**Issue:** REST API routes (e.g., `api_create_application`) lack the rate limiting applied to web forms.
**Fix:** Add consistent rate limits across all write endpoints.

---

## 🟢 Low Severity

### 10. Mixed CRUD Patterns
**File:** `app/models.py`
**Issue:** Some CRUD functions take `sqlite3.Connection`, others take the Flask app and manage connections internally.
**Fix:** Standardize on one pattern throughout the module.

### 11. Duplicate Table Definitions
**File:** `app/database.py:84`
**Issue:** Duplicate table definitions for `gmail_tokens` and `gmail_connections` from a transition phase.
**Fix:** Drop the unused table.

### 12. Routes File Bloat
**File:** `app/routes.py:1` (600+ lines)
**Issue:** File contains business logic that should be in service modules.
**Fix:** Extract email sync orchestration, application matching, and parsing workflows into dedicated service modules.

### 13. Duplicated String Cleaning
**Files:** `app/utils.py:11` vs `app/email_parser.py:332`
**Issue:** `clean_string` and `clean_company` are re-implemented instead of reused.
**Fix:** Import from utils instead of duplicating logic.

### 14. Unprotected Schema Migrations
**File:** `app/database.py:120`
**Issue:** Column additions happen outside an explicit transaction. If the process crashes mid-migration, the schema could be left inconsistent.
**Fix:** Wrap schema changes in an explicit transaction.

### 15. Limited Date Parsing
**File:** `app/email_parser.py:270`
**Issue:** `extract_date` regex handles only three formats. Fails on regional formats, relative dates ("today", "last week"), or common human strings.
**Fix:** Consider using a library like `dateparser` or extend regex patterns.

### 16. Hardcoded Config in Background Thread
**File:** `app/gmail.py:214`
**Issue:** Sync loop reads parser settings from `os.getenv` instead of `app.config` or per-user DB settings.
**Fix:** Use app context or per-user configuration so behavior can change without restarting.
