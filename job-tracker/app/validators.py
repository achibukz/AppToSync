"""Validation and normalization functions for the Job Tracker application."""

from datetime import date
from typing import Any

from app.config import SOURCE_OPTIONS, SOURCE_TYPE_OPTIONS, STATUS_OPTIONS
from app.utils import clean_string, to_float


def normalize_payload(payload: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    """Normalize and validate application payload data.
    
    Args:
        payload: Raw payload data
        partial: If True, only include non-None values (for updates)
        
    Returns:
        Normalized payload dictionary
        
    Raises:
        ValueError: If required fields are missing or invalid
    """
    normalized = {
        "company": clean_string(payload.get("company")),
        "role": clean_string(payload.get("role")),
        "job_url": clean_string(payload.get("job_url")),
        "source": clean_string(payload.get("source")) or "Other",
        "status": clean_string(payload.get("status")) or "Applied",
        "applied_date": clean_string(payload.get("applied_date")) or date.today().isoformat(),
        "salary_min": to_float(payload.get("salary_min")),
        "salary_max": to_float(payload.get("salary_max")),
        "salary_currency": clean_string(payload.get("salary_currency")) or "PHP",
        "notes": clean_string(payload.get("notes")),
        "follow_up_date": clean_string(payload.get("follow_up_date")),
        "source_type": clean_string(payload.get("source_type")) or "manual",
    }

    if partial:
        return {key: value for key, value in normalized.items() if value is not None}

    if not normalized["company"] or not normalized["role"]:
        raise ValueError("Company and role are required.")

    if normalized["status"] not in STATUS_OPTIONS:
        raise ValueError("Invalid status.")

    if normalized["source"] not in SOURCE_OPTIONS:
        normalized["source"] = "Other"

    if normalized["source_type"] not in SOURCE_TYPE_OPTIONS:
        normalized["source_type"] = "manual"

    return normalized


def form_payload(form: Any) -> dict[str, Any]:
    """Convert form data to normalized payload.
    
    Args:
        form: Flask request.form object
        
    Returns:
        Normalized payload dictionary
    """
    return normalize_payload(form)
