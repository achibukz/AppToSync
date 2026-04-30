"""Configuration and constants for the Job Tracker application."""

# Status options for job applications
STATUS_OPTIONS = [
    "Applied",
    "Interview Scheduled",
    "Technical Test",
    "Final Interview",
    "Offer Received",
    "Rejected",
    "Ghosted",
]

# Source options for job applications
SOURCE_OPTIONS = ["LinkedIn", "Indeed", "Prosple", "Direct", "Other"]

# Source type options for how applications are captured
SOURCE_TYPE_OPTIONS = ["gmail", "chrome_extension", "manual"]

# Gemini model options exposed in the parser UI
GEMINI_MODEL_OPTIONS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

DEFAULT_GEMINI_MODEL = GEMINI_MODEL_OPTIONS[1]

GROQ_MODEL_OPTIONS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
DEFAULT_GROQ_MODEL = GROQ_MODEL_OPTIONS[0]

PARSER_PROVIDER_OPTIONS = ["gemini", "groq"]
DEFAULT_PARSER_PROVIDER = "gemini"

PARSER_MODEL_CHOICES = [
    ("gemini:gemini-2.5-flash",      "Gemini · 2.5 Flash",             "gemini", "gemini-2.5-flash"),
    ("gemini:gemini-2.5-flash-lite", "Gemini · 2.5 Flash Lite",        "gemini", "gemini-2.5-flash-lite"),
    ("groq:llama-3.3-70b-versatile", "Groq · Llama 3.3 70B Versatile", "groq",   "llama-3.3-70b-versatile"),
    ("groq:llama-3.1-8b-instant",    "Groq · Llama 3.1 8B Instant",    "groq",   "llama-3.1-8b-instant"),
]
DEFAULT_PARSER_CHOICE = "gemini:gemini-2.5-flash-lite"

# Gmail OAuth and polling settings.
import os

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_REDIRECT_URI = os.environ.get("GMAIL_REDIRECT_URI", "http://127.0.0.1:3000/gmail/callback")
GMAIL_SYNC_INTERVAL_MINUTES = 15
GMAIL_POLL_INTERVAL_SECONDS = 60

# CSS styling classes for different statuses
STATUS_STYLES = {
    "Applied": "blue",
    "Interview Scheduled": "yellow",
    "Technical Test": "orange",
    "Final Interview": "purple",
    "Offer Received": "green",
    "Rejected": "red",
    "Ghosted": "gray",
}

# Month name to number mapping
MONTHS = {
    month.lower(): index
    for index, month in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

# Sample data for seeding the database
DEMO_APPLICATIONS = [
    {
        "company": "Canva",
        "role": "Product Designer",
        "job_url": "https://www.canva.com/careers/",
        "source": "Direct",
        "status": "Interview Scheduled",
        "applied_date": "2026-04-19",
        "follow_up_date": "2026-05-01",
        "source_type": "manual",
        "notes": "Recruiter intro call booked.",
    },
    {
        "company": "Atlassian",
        "role": "Software Engineer Intern",
        "job_url": "https://www.atlassian.com/company/careers",
        "source": "LinkedIn",
        "status": "Applied",
        "applied_date": "2026-04-21",
        "follow_up_date": "2026-05-03",
        "source_type": "chrome_extension",
        "notes": "Captured from the browser extension flow.",
    },
    {
        "company": "Figma",
        "role": "Community Advocate",
        "job_url": "https://www.figma.com/careers/",
        "source": "Indeed",
        "status": "Rejected",
        "applied_date": "2026-04-10",
        "source_type": "gmail",
        "notes": "Auto-detected from Gmail rejection email.",
    },
]
