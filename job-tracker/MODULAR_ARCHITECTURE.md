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
