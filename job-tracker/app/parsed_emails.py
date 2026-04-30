"""Parsed-email queue CRUD.

Each Gmail message that the sync pipeline encounters is stored as a row here.
The row tracks the raw email metadata (sender/subject/body), the parser
output, and where the email currently stands in the review queue.

Status values:
- paused          : fetched but not yet parsed (or parser failed; will retry)
- pending_review  : parsed and job-related; would create a NEW application — awaiting accept/reject
- auto_updated    : parsed and routed to an existing application (watcher or fuzzy match)
- not_job         : parsed and not job-related; ignored
- accepted        : user accepted; an application was created from this email
- dismissed       : user rejected; never resurface
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.utils import utc_now


PARSE_STATUSES = {
    "paused",
    "pending_review",
    "auto_updated",
    "not_job",
    "accepted",
    "dismissed",
}


def upsert_email_record(
    connection: sqlite3.Connection,
    *,
    gmail_message_id: str,
    received_at: str | None,
    from_address: str | None,
    subject: str | None,
    body_text: str | None,
) -> bool:
    """Insert a new email row. Returns True if inserted, False if it already existed."""
    timestamp = utc_now()
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO parsed_emails (
            gmail_message_id, received_at, from_address, subject, body_text,
            parse_status, parse_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'paused', 0, ?, ?)
        """,
        (gmail_message_id, received_at, from_address, subject, body_text, timestamp, timestamp),
    )
    return cursor.rowcount > 0


def fetch_email(connection: sqlite3.Connection, gmail_message_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM parsed_emails WHERE gmail_message_id = ?",
        (gmail_message_id,),
    ).fetchone()
    return dict(row) if row else None


def fetch_emails_needing_parse(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all paused emails (i.e. those that should be parsed on the next sync)."""
    rows = connection.execute(
        "SELECT * FROM parsed_emails WHERE parse_status = 'paused' ORDER BY received_at ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_pending_review(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM parsed_emails WHERE parse_status = 'pending_review' ORDER BY received_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_paused(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM parsed_emails
         WHERE parse_status = 'paused' AND parse_attempts > 0
         ORDER BY received_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def update_parse_failure(
    connection: sqlite3.Connection,
    gmail_message_id: str,
    error: str,
) -> None:
    """Record a parse attempt that failed (Gemini error). Keeps status=paused."""
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE parsed_emails
           SET parse_status = 'paused',
               parse_error = ?,
               parse_attempts = parse_attempts + 1,
               last_parsed_at = ?,
               updated_at = ?
         WHERE gmail_message_id = ?
        """,
        (error, timestamp, timestamp, gmail_message_id),
    )


def update_parse_success(
    connection: sqlite3.Connection,
    gmail_message_id: str,
    *,
    parse_status: str,
    is_job_related: bool,
    parsed_company: str | None,
    parsed_role: str | None,
    parsed_status: str | None,
    parsed_confidence: float | None,
    parsed_reasoning: str | None,
    application_id: str | None = None,
) -> None:
    """Record a successful parse and the routing decision."""
    if parse_status not in PARSE_STATUSES:
        raise ValueError(f"Invalid parse_status: {parse_status}")
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE parsed_emails
           SET parse_status = ?,
               parse_error = NULL,
               parse_attempts = parse_attempts + 1,
               last_parsed_at = ?,
               is_job_related = ?,
               parsed_company = ?,
               parsed_role = ?,
               parsed_status = ?,
               parsed_confidence = ?,
               parsed_reasoning = ?,
               application_id = COALESCE(?, application_id),
               updated_at = ?
         WHERE gmail_message_id = ?
        """,
        (
            parse_status,
            timestamp,
            1 if is_job_related else 0,
            parsed_company,
            parsed_role,
            parsed_status,
            parsed_confidence,
            parsed_reasoning,
            application_id,
            timestamp,
            gmail_message_id,
        ),
    )


def mark_accepted(
    connection: sqlite3.Connection,
    gmail_message_id: str,
    application_id: str,
) -> None:
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE parsed_emails
           SET parse_status = 'accepted',
               application_id = ?,
               updated_at = ?
         WHERE gmail_message_id = ?
        """,
        (application_id, timestamp, gmail_message_id),
    )


def mark_dismissed(connection: sqlite3.Connection, gmail_message_id: str) -> None:
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE parsed_emails
           SET parse_status = 'dismissed',
               updated_at = ?
         WHERE gmail_message_id = ?
        """,
        (timestamp, gmail_message_id),
    )


def mark_for_retry(connection: sqlite3.Connection, gmail_message_id: str) -> None:
    """Force a paused/queued email back into the parse queue."""
    timestamp = utc_now()
    connection.execute(
        """
        UPDATE parsed_emails
           SET parse_status = 'paused',
               updated_at = ?
         WHERE gmail_message_id = ?
        """,
        (timestamp, gmail_message_id),
    )
