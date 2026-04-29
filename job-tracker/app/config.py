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
