"""Application-scoped email watcher CRUD.

Each watcher maps a sender email or domain pattern to a specific application row.
During Gmail sync, a watcher match routes the email to that application so the
parser can update its status — without overriding the company name.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from typing import Any

from app.utils import utc_now


def set_watchers_for_application(
    connection: sqlite3.Connection,
    application_id: str,
    patterns: list[str],
) -> None:
    """Replace all watchers for an application with the given pattern list."""
    connection.execute(
        "DELETE FROM application_watchers WHERE application_id = ?",
        (application_id,),
    )
    timestamp = utc_now()
    seen: set[str] = set()
    for raw in patterns:
        pattern = _normalise(raw)
        if pattern and pattern not in seen:
            seen.add(pattern)
            connection.execute(
                """
                INSERT INTO application_watchers (id, application_id, sender_pattern, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), application_id, pattern, timestamp),
            )


def fetch_watchers_for_application(
    connection: sqlite3.Connection,
    application_id: str,
) -> list[str]:
    """Return the sender patterns registered for an application, ordered."""
    rows = connection.execute(
        "SELECT sender_pattern FROM application_watchers WHERE application_id = ? ORDER BY sender_pattern",
        (application_id,),
    ).fetchall()
    return [row["sender_pattern"] for row in rows]


def delete_watchers_for_application(
    connection: sqlite3.Connection,
    application_id: str,
) -> None:
    """Remove all watchers for an application (called on application delete)."""
    connection.execute(
        "DELETE FROM application_watchers WHERE application_id = ?",
        (application_id,),
    )


def match_application_by_sender(
    connection: sqlite3.Connection,
    sender: str,
    user_id: int | None = None,
) -> str | None:
    """Return the application_id whose watcher matches *sender*, or None.

    Matching rules (case-insensitive):
    - Domain pattern  "@domain.com"  — sender email must end with this suffix.
    - Address pattern "user@host"    — sender string must contain this substring.

    The first match wins. Handles "Display Name <user@host>" format.
    """
    if not sender:
        return None

    sender_lower = sender.lower()
    extracted = _extract_email(sender_lower)

    if user_id is not None:
        rows = connection.execute(
            """
            SELECT aw.application_id, aw.sender_pattern
              FROM application_watchers aw
              JOIN applications a ON a.id = aw.application_id
             WHERE a.user_id = ?
            """,
            (user_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT application_id, sender_pattern FROM application_watchers"
        ).fetchall()

    for row in rows:
        pattern = row["sender_pattern"]
        if pattern.startswith("@"):
            if (extracted and extracted.endswith(pattern)) or sender_lower.endswith(pattern):
                return row["application_id"]
        else:
            if pattern in sender_lower:
                return row["application_id"]

    return None


def _extract_email(sender: str) -> str | None:
    """Pull the bare address out of a 'Display Name <addr>' string."""
    match = re.search(r"<([^>]+)>", sender)
    if match:
        return match.group(1).strip()
    bare = sender.strip()
    return bare if bare else None


def _normalise(pattern: str) -> str:
    return pattern.strip().lower()
