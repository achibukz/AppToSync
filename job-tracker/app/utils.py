"""Utility functions for the Job Tracker application."""

import re
from datetime import date, datetime
from typing import Any


def utc_now() -> str:
    """Return the current UTC time in ISO format with Z suffix."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def clean_string(value: Any) -> str | None:
    """Clean and normalize string input.
    
    Args:
        value: Input value to clean
        
    Returns:
        Cleaned string or None if empty
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_company(value: str) -> str:
    """Clean company name by removing extra whitespace and punctuation.
    
    Args:
        value: Company name to clean
        
    Returns:
        Cleaned company name
    """
    cleaned = re.sub(r"\s+", " ", value).strip().strip(".,")
    return cleaned


def to_float(value: Any) -> float | None:
    """Convert value to float, returning None if not possible.
    
    Args:
        value: Value to convert
        
    Returns:
        Float value or None
    """
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_list(items: list[str], limit: int | None = 5) -> str:
    """Format a list of strings into a human-readable string with an optional limit.
    
    Example: ["A", "B", "C"] -> "A, B, and C"
    Example with limit=2: ["A", "B", "C"] -> "A, B, and 1 other"
    
    Args:
        items: List of strings to format
        limit: Maximum number of items to show before truncating
        
    Returns:
        Formatted string
    """
    if not items:
        return ""
    
    count = len(items)
    if limit is not None and count > limit:
        visible = items[:limit]
        others = count - limit
        suffix = "other" if others == 1 else "others"
        
        if limit == 1:
            return f"{visible[0]} and {others} {suffix}"
        return f"{', '.join(visible)}, and {others} {suffix}"

    if count == 1:
        return items[0]
    if count == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def parse_date(value: str | None) -> date | None:
    """Parse ISO format date string to date object.
    
    Args:
        value: ISO format date string
        
    Returns:
        date object or None if parsing fails
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
