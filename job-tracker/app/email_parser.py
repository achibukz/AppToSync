"""Email parsing functionality for extracting job application details."""

import json
import os
import re
from typing import Any

from google import genai
from groq import Groq

from app.config import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GROQ_MODEL,
    GEMINI_MODEL_OPTIONS,
    GROQ_MODEL_OPTIONS,
    MONTHS,
)


def parse_job_email(
    email_text: str,
    provider: str = "local",
    gemini_model: str | None = None,
    groq_model: str | None = None,
) -> dict[str, Any]:
    """Parse a job-related email using the specified provider.

    Falls back to local parsing if the AI provider fails.
    """
    provider = (provider or "local").strip().lower()
    selected_gemini = normalize_gemini_model(gemini_model)
    selected_groq = normalize_groq_model(groq_model)

    if provider == "gemini":
        result, error = gemini_parse_job_email_with_error(email_text, model=selected_gemini)
        if result is not None:
            return {
                **result,
                "provider": provider,
                "provider_used": "gemini",
                "provider_error": None,
                "gemini_model": selected_gemini,
                "groq_model": None,
            }
        local_result = local_parse_job_email(email_text)
        return {
            **local_result,
            "provider": provider,
            "provider_used": "local",
            "provider_error": error or "Gemini request failed or GEMINI_API_KEY was missing; local parser was used instead.",
            "gemini_model": selected_gemini,
            "groq_model": None,
        }

    if provider == "groq":
        result, error = groq_parse_job_email_with_error(email_text, model=selected_groq)
        if result is not None:
            return {
                **result,
                "provider": provider,
                "provider_used": "groq",
                "provider_error": None,
                "gemini_model": None,
                "groq_model": selected_groq,
            }
        local_result = local_parse_job_email(email_text)
        return {
            **local_result,
            "provider": provider,
            "provider_used": "local",
            "provider_error": error or "Groq request failed or GROQ_API_KEY was missing; local parser was used instead.",
            "gemini_model": None,
            "groq_model": selected_groq,
        }

    local_result = local_parse_job_email(email_text)
    return {
        **local_result,
        "provider": provider,
        "provider_used": "local",
        "provider_error": None,
        "gemini_model": selected_gemini,
        "groq_model": None,
    }


def parse_job_email_strict(
    email_text: str,
    provider: str = "gemini",
    gemini_model: str | None = None,
    groq_model: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse an email using an AI provider only, with no local fallback.

    Returns ``(result, error)``. On failure the caller should pause and retry.
    """
    provider = (provider or "gemini").strip().lower()

    if provider == "groq":
        selected_model = normalize_groq_model(groq_model)
        result, error = groq_parse_job_email_with_error(email_text, model=selected_model)
        if result is None:
            return None, error
        return {
            **result,
            "provider": "groq",
            "provider_used": "groq",
            "provider_error": None,
            "gemini_model": None,
            "groq_model": selected_model,
        }, None

    selected_model = normalize_gemini_model(gemini_model)
    result, error = gemini_parse_job_email_with_error(email_text, model=selected_model)
    if result is None:
        return None, error
    return {
        **result,
        "provider": "gemini",
        "provider_used": "gemini",
        "provider_error": None,
        "gemini_model": selected_model,
        "groq_model": None,
    }, None


def _build_parse_prompt(email_text: str) -> str:
    return (
        "Extract structured job application details from this email. "
        "Return only valid JSON with these keys: is_job_related (boolean), company (string|null), "
        "role (string|null), status (string|null), interview_date (string|null, ISO format), "
        "applied_date (string|null, ISO format — the date the application was submitted; "
        "only populate if status is Applied, otherwise null), "
        "source (string|null — exactly one of: LinkedIn, Indeed, Prosple, Direct, Other; "
        "infer from sender address domain, URLs in the email, or explicit platform mentions; "
        "null if not determinable), "
        "confidence (number), extracted_by (string), reasoning_summary (string), field_explanations (object). "
        "Use status values from: Applied, Interview Scheduled, Technical Test, Final Interview, "
        "Offer Received, Rejected, Ghosted. "
        "If the email is not related to a job application, set is_job_related to false and other fields to null where appropriate. "
        "Keep reasoning_summary and field_explanations concise and factual. Do not provide hidden chain-of-thought. "
        "No markdown, no code fences, no extra text. Email:\n\n"
        f"{email_text.strip()}"
    )


def groq_parse_job_email_with_error(
    email_text: str,
    model: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse email using Groq, returning both result and error."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "Groq request failed because GROQ_API_KEY was not set."
    selected_model = normalize_groq_model(model)
    prompt = _build_parse_prompt(email_text)
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

    if not text:
        return None, "Groq response did not include parsed text."

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "Groq response text was not valid JSON."

    return {
        "is_job_related": bool(parsed.get("is_job_related", True)),
        "company": parsed.get("company"),
        "role": parsed.get("role"),
        "status": parsed.get("status"),
        "interview_date": parsed.get("interview_date"),
        "applied_date": parsed.get("applied_date"),
        "source": parsed.get("source"),
        "confidence": parsed.get("confidence", 0.0),
        "extracted_by": "groq",
        "reasoning_summary": parsed.get("reasoning_summary"),
        "field_explanations": parsed.get("field_explanations") or {},
    }, None


def gemini_parse_job_email(email_text: str, model: str | None = None) -> dict[str, Any] | None:
    """Parse email using Gemini AI.
    
    Args:
        email_text: Email content to parse
        model: Gemini model to use
        
    Returns:
        Parsed job details or None if parsing failed
    """
    gemini_result, _ = gemini_parse_job_email_with_error(email_text, model=model)
    return gemini_result


def gemini_parse_job_email_with_error(
    email_text: str,
    model: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse email using Gemini AI, returning both result and error.
    
    Args:
        email_text: Email content to parse
        model: Gemini model to use
        
    Returns:
        Tuple of (parsed_result, error_message)
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None, "Gemini request failed because GEMINI_API_KEY or GOOGLE_API_KEY was not set."
    selected_model = normalize_gemini_model(model)
    prompt = _build_parse_prompt(email_text)

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=selected_model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        text = response.text
    except Exception as exc:
        return None, f"Gemini request failed: {exc}"

    if not text:
        return None, "Gemini response did not include parsed text."

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "Gemini response text was not valid JSON."

    return {
        "is_job_related": bool(parsed.get("is_job_related", True)),
        "company": parsed.get("company"),
        "role": parsed.get("role"),
        "status": parsed.get("status"),
        "interview_date": parsed.get("interview_date"),
        "applied_date": parsed.get("applied_date"),
        "source": parsed.get("source"),
        "confidence": parsed.get("confidence", 0.0),
        "extracted_by": parsed.get("extracted_by", "gemini"),
        "reasoning_summary": parsed.get("reasoning_summary"),
        "field_explanations": parsed.get("field_explanations") or {},
    }, None


def local_parse_job_email(email_text: str) -> dict[str, Any]:
    """Parse email using local heuristics and regex patterns.
    
    Args:
        email_text: Email content to parse
        
    Returns:
        Dictionary with extracted job details
    """
    text = email_text.strip()
    normalized = f" {text.lower()} "
    job_related = looks_job_related(normalized)
    if not job_related:
        return {
            "is_job_related": False,
            "company": None,
            "role": None,
            "status": None,
            "interview_date": None,
            "confidence": 0.12,
            "extracted_by": "heuristic",
        }

    company = extract_company(text)
    role = extract_role(text)
    status = detect_status(normalized)
    interview_date = extract_date(text)
    source = detect_source(normalized)
    applied_date = extract_date(text) if status == "Applied" else None

    confidence = 0.54
    if company:
        confidence += 0.16
    if role:
        confidence += 0.16
    if status and status != "Applied":
        confidence += 0.08
    if interview_date:
        confidence += 0.06

    return {
        "is_job_related": True,
        "company": company,
        "role": role,
        "status": status,
        "interview_date": interview_date,
        "applied_date": applied_date,
        "source": source,
        "confidence": round(min(confidence, 0.98), 2),
        "extracted_by": "heuristic",
    }


def looks_job_related(text: str) -> bool:
    """Check if email appears to be job-related using keyword matching.
    
    Args:
        text: Lowercased email text
        
    Returns:
        True if email appears job-related
    """
    keywords = [
        "application received",
        "thanks for applying",
        "interview",
        "assessment",
        "technical test",
        "take-home",
        "offer",
        "regret",
        "candidate",
        "recruiter",
    ]
    return any(keyword in text for keyword in keywords)


def detect_status(text: str) -> str:
    """Detect job application status from email text.
    
    Args:
        text: Lowercased email text
        
    Returns:
        Detected status or 'Applied' as default
    """
    rules = [
        ("Offer Received", ["offer", "congratulations", "welcome aboard"]),
        ("Final Interview", ["final interview", "last round", "final round"]),
        ("Technical Test", ["technical test", "coding challenge", "assessment", "take-home", "take home", "hackerrank"]),
        ("Interview Scheduled", ["interview", "availability", "calendar invite", "meeting link"]),
        ("Rejected", ["regret", "unfortunately", "moved forward with other candidates", "not moving forward"]),
        ("Applied", ["thanks for applying", "application received", "we received your application"]),
    ]
    for status, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return status
    return "Applied"


def detect_source(text: str) -> str | None:
    """Detect job platform from email text using keyword/domain matching."""
    if "linkedin.com" in text or " linkedin " in text:
        return "LinkedIn"
    if "indeed.com" in text or " indeed " in text:
        return "Indeed"
    if "prosple.com" in text or " prosple " in text:
        return "Prosple"
    return None


def extract_company(text: str) -> str | None:
    """Extract company name from email text.
    
    Args:
        text: Email text
        
    Returns:
        Extracted company name or None
    """
    paired_patterns = [
        r"thanks for applying to (?P<role>.+?) at (?P<company>[A-Z][A-Za-z0-9&'().\-/ ]{1,60}?)(?=\.|,|;|:|\s+we\b|\s+you\b|$)",
        r"role[:\-]\s*(?P<role>.+?)\s+at\s+(?P<company>[A-Z][A-Za-z0-9&'().\-/ ]{1,60}?)(?=\.|,|;|:|\s+we\b|\s+you\b|$)",
    ]
    for pattern in paired_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_company(match.group("company"))

    fallback_patterns = [
        r"company[:\-]\s*([A-Z][A-Za-z0-9&'().\-/ ]{1,60}?)(?=\.|,|;|:|\s+we\b|\s+you\b|$)",
        r"from\s+([A-Z][A-Za-z0-9&'().\-/ ]{1,60}?)(?=\.|,|;|:|\s+we\b|\s+you\b|$)",
        r"at\s+([A-Z][A-Za-z0-9&'().\-/ ]{1,60}?)(?=\.|,|;|:|\s+we\b|\s+you\b|$)",
    ]
    for pattern in fallback_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_company(match.group(1))
    return None


def extract_role(text: str) -> str | None:
    """Extract job role from email text.
    
    Args:
        text: Email text
        
    Returns:
        Extracted role or None
    """
    patterns = [
        r"for the role of ([A-Za-z0-9&'().\-/ ]{2,80})",
        r"role[:\-]\s*([A-Za-z0-9&'().\-/ ]{2,80})",
        r"position[:\-]\s*([A-Za-z0-9&'().\-/ ]{2,80})",
        r"job title[:\-]\s*([A-Za-z0-9&'().\-/ ]{2,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_company(match.group(1))
    return None


def extract_date(text: str) -> str | None:
    """Extract date from email text.
    
    Handles ISO format, slash format, and month name format.
    
    Args:
        text: Email text
        
    Returns:
        Date in ISO format or None
    """
    # Try ISO format
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)

    # Try slash format
    slash_match = re.search(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b", text)
    if slash_match:
        month, day, year = slash_match.group(1).split("/")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    # Try month name format
    month_match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:,\s*(20\d{2}))?\b",
        text,
        flags=re.IGNORECASE,
    )
    if month_match:
        from datetime import datetime
        month_name, day, year = month_match.groups()
        return f"{int(year or datetime.utcnow().year):04d}-{MONTHS[month_name.lower()]:02d}-{int(day):02d}"

    return None


def clean_company(value: str) -> str:
    """Clean company/role name by removing extra whitespace and punctuation.
    
    Args:
        value: Text to clean
        
    Returns:
        Cleaned text
    """
    cleaned = re.sub(r"\s+", " ", value).strip().strip(".,")
    return cleaned


def normalize_gemini_model(model: str | None) -> str:
    """Normalize Gemini model input to a supported option."""
    candidate = (model or DEFAULT_GEMINI_MODEL).strip()
    if candidate in GEMINI_MODEL_OPTIONS:
        return candidate
    return DEFAULT_GEMINI_MODEL


def normalize_groq_model(model: str | None) -> str:
    """Normalize Groq model input to a supported option."""
    candidate = (model or DEFAULT_GROQ_MODEL).strip()
    if candidate in GROQ_MODEL_OPTIONS:
        return candidate
    return DEFAULT_GROQ_MODEL
