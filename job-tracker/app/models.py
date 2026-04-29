"""Database models and CRUD operations for job applications."""

import sqlite3
import uuid
from datetime import date
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
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    connection.execute(
        """
        INSERT INTO applications (
            id, company, role, job_url, source, status, applied_date,
            salary_min, salary_max, salary_currency, notes,
            follow_up_date, source_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            source_type = ?, updated_at = ?
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
            merged["updated_at"],
            application_id,
        ),
    )
    return fetch_application_by_id(connection, application_id)


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
    app: Flask, filters: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Fetch all applications with optional filtering.
    
    Args:
        app: Flask application instance
        filters: Optional dict with 'status', 'source', and/or 'search' keys
        
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

        query.append("ORDER BY date(applied_date) DESC, company ASC")
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
