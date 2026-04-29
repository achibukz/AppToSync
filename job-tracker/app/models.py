"""Database models and CRUD operations for job applications."""

import re
import sqlite3
import uuid
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from flask import Flask

from app.database import connect_db
from app.utils import parse_date, utc_now
from app.config import STATUS_OPTIONS, STATUS_STYLES


def insert_application(
    connection: sqlite3.Connection, payload: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    """Insert a new application into the database.
    
    Args:
        connection: Database connection
        payload: Application data
        timestamp: Creation timestamp
        
    Returns:
        Created application dictionary
    """
    application = {
        "id": str(uuid.uuid4()),
        "company": payload["company"],
        "role": payload["role"],
        "job_url": payload.get("job_url"),
        "source": payload.get("source", "Other"),
        "status": payload.get("status", "Applied"),
        "applied_date": payload.get("applied_date", date.today().isoformat()),
        "salary_min": payload.get("salary_min"),
        "salary_max": payload.get("salary_max"),
        "salary_currency": payload.get("salary_currency", "PHP"),
        "notes": payload.get("notes"),
        "follow_up_date": payload.get("follow_up_date"),
        "source_type": payload.get("source_type", "manual"),
        "gmail_message_id": payload.get("gmail_message_id"),
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    connection.execute(
        """
        INSERT INTO applications (
            id, company, role, job_url, source, status, applied_date,
            salary_min, salary_max, salary_currency, notes,
            follow_up_date, source_type, gmail_message_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application["id"],
            application["company"],
            application["role"],
            application["job_url"],
            application["source"],
            application["status"],
            application["applied_date"],
            application["salary_min"],
            application["salary_max"],
            application["salary_currency"],
            application["notes"],
            application["follow_up_date"],
            application["source_type"],
            application["gmail_message_id"],
            application["created_at"],
            application["updated_at"],
        ),
    )
    return application


def update_application(
    connection: sqlite3.Connection,
    application_id: str,
    payload: dict[str, Any],
    partial: bool = False,
) -> dict[str, Any] | None:
    """Update an existing application.
    
    Args:
        connection: Database connection
        application_id: ID of application to update
        payload: Updated application data
        partial: If True, only update provided fields
        
    Returns:
        Updated application dictionary or None if not found
        
    Raises:
        ValueError: If validation fails
    """
    existing = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    if existing is None:
        return None

    merged = dict(existing)
    merged.update(payload)
    merged["gmail_message_id"] = payload.get("gmail_message_id", merged.get("gmail_message_id"))
    if not partial:
        if not merged.get("company") or not merged.get("role"):
            raise ValueError("Company and role are required.")
        if merged.get("status") not in STATUS_OPTIONS:
            raise ValueError("Invalid status.")

    merged["updated_at"] = utc_now()

    connection.execute(
        """
        UPDATE applications
        SET company = ?, role = ?, job_url = ?, source = ?, status = ?, applied_date = ?,
            salary_min = ?, salary_max = ?, salary_currency = ?, notes = ?, follow_up_date = ?,
            source_type = ?, gmail_message_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            merged["company"],
            merged["role"],
            merged.get("job_url"),
            merged.get("source", "Other"),
            merged.get("status", "Applied"),
            merged.get("applied_date", date.today().isoformat()),
            merged.get("salary_min"),
            merged.get("salary_max"),
            merged.get("salary_currency", "PHP"),
            merged.get("notes"),
            merged.get("follow_up_date"),
            merged.get("source_type", "manual"),
            merged.get("gmail_message_id"),
            merged["updated_at"],
            application_id,
        ),
    )
    return fetch_application_by_id(connection, application_id)


def fetch_application_by_gmail_message_id(
    connection: sqlite3.Connection, gmail_message_id: str | None
) -> dict[str, Any] | None:
    """Fetch a single application by Gmail message ID."""
    if not gmail_message_id:
        return None
    row = connection.execute(
        "SELECT * FROM applications WHERE gmail_message_id = ?",
        (gmail_message_id,),
    ).fetchone()
    return serialize_application(row) if row else None


def find_fuzzy_application(
    connection: sqlite3.Connection,
    company: str,
    role: str,
    threshold: float = 0.85,
) -> dict[str, Any] | None:
    """Find an application by fuzzy company and role matching."""
    rows = connection.execute("SELECT * FROM applications").fetchall()
    best_match: sqlite3.Row | None = None
    best_score = 0.0

    normalized_company = _normalize_match_value(company)
    normalized_role = _normalize_match_value(role)

    for row in rows:
        company_score = SequenceMatcher(
            None, normalized_company, _normalize_match_value(row["company"])
        ).ratio()
        role_score = SequenceMatcher(None, normalized_role, _normalize_match_value(row["role"])).ratio()
        if company_score < 0.78 or role_score < 0.72:
            continue

        score = (company_score * 0.6) + (role_score * 0.4)
        if score > best_score:
            best_score = score
            best_match = row

    if best_match is None or best_score < threshold:
        return None
    return serialize_application(best_match)


def delete_application(app: Flask, application_id: str) -> bool:
    """Delete an application from the database.
    
    Args:
        app: Flask application instance
        application_id: ID of application to delete
        
    Returns:
        True if application was deleted, False if not found
    """
    connection = connect_db(app)
    try:
        cursor = connection.execute("DELETE FROM applications WHERE id = ?", (application_id,))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def fetch_application(app: Flask, application_id: str | None) -> dict[str, Any] | None:
    """Fetch a single application by ID.
    
    Args:
        app: Flask application instance
        application_id: ID of application to fetch
        
    Returns:
        Application dictionary or None if not found
    """
    if not application_id:
        return None
    connection = connect_db(app)
    try:
        return fetch_application_by_id(connection, application_id)
    finally:
        connection.close()


def fetch_application_by_id(connection: sqlite3.Connection, application_id: str) -> dict[str, Any] | None:
    """Fetch a single application by ID using an existing connection.
    
    Args:
        connection: Database connection
        application_id: ID of application to fetch
        
    Returns:
        Application dictionary or None if not found
    """
    row = connection.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    return serialize_application(row) if row else None


def fetch_applications(
    app: Flask, filters: dict[str, str] | None = None, sort_by: str | None = None, order: str | None = "desc"
) -> list[dict[str, Any]]:
    """Fetch all applications with optional filtering and sorting.
    
    Args:
        app: Flask application instance
        filters: Optional dict with 'status', 'source', and/or 'search' keys
        sort_by: Column name to sort by
        order: Sort direction ('asc' or 'desc')
        
    Returns:
        List of application dictionaries
    """
    filters = filters or {}
    connection = connect_db(app)
    try:
        query = ["SELECT * FROM applications WHERE 1 = 1"]
        parameters: list[Any] = []

        if filters.get("status"):
            query.append("AND status = ?")
            parameters.append(filters["status"])
        if filters.get("source"):
            query.append("AND source = ?")
            parameters.append(filters["source"])
        if filters.get("search"):
            query.append("AND (LOWER(company) LIKE ? OR LOWER(role) LIKE ?)")
            search_value = f"%{filters['search'].lower()}%"
            parameters.extend([search_value, search_value])

        # Sorting logic
        valid_columns = {
            "company": "company COLLATE NOCASE",
            "role": "role COLLATE NOCASE",
            "status": "status COLLATE NOCASE",
            "applied_date": "date(applied_date)",
            "source": "source COLLATE NOCASE",
            "follow_up_date": "date(follow_up_date)",
            "salary": "salary_min",
        }
        
        db_column = valid_columns.get(sort_by, "date(applied_date)")
        db_order = "ASC" if order and order.lower() == "asc" else "DESC"
        
        # secondary sort by company also needs NOCASE for consistency
        secondary_sort = "company COLLATE NOCASE ASC" if sort_by != "company" else "role COLLATE NOCASE ASC"
        
        query.append(f"ORDER BY {db_column} {db_order}, {secondary_sort}")
        rows = connection.execute(" ".join(query), parameters).fetchall()
        return [serialize_application(row) for row in rows]
    finally:
        connection.close()


def serialize_application(row: sqlite3.Row | None) -> dict[str, Any]:
    """Convert database row to application dictionary.
    
    Adds computed fields like is_overdue and status_class.
    
    Args:
        row: Database row from sqlite3.Row
        
    Returns:
        Application dictionary with computed fields
    """
    if row is None:
        return {}
    data = dict(row)
    follow_up = parse_date(data.get("follow_up_date"))
    data["is_overdue"] = bool(follow_up and follow_up < date.today())
    data["status_class"] = STATUS_STYLES.get(data.get("status", "Applied"), "gray")
    return data


def _normalize_match_value(value: str | None) -> str:
    """Normalize text for fuzzy matching."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
