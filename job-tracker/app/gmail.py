"""Gmail OAuth and sync integration for the Job Tracker application."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask

from app.config import (
    DEFAULT_GEMINI_MODEL,
    GMAIL_POLL_INTERVAL_SECONDS,
    GMAIL_REDIRECT_URI,
    GMAIL_SCOPES,
    GMAIL_SYNC_INTERVAL_MINUTES,
)
from app.database import connect_db
from app.email_parser import parse_job_email
from app.models import (
    fetch_application_by_gmail_message_id,
    find_fuzzy_application,
    insert_application,
    update_application,
)
from app.utils import clean_company, clean_string, utc_now

try:  # pragma: no cover - optional dependency guard
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
except Exception:  # pragma: no cover - optional dependency guard
    Request = None
    Credentials = None
    Flow = None
    build = None

_POLL_STARTED = False
_POLL_LOCK = threading.Lock()


def gmail_is_configured() -> bool:
    """Return whether Gmail OAuth environment variables are available."""
    return bool(os.getenv("GMAIL_CLIENT_ID") and os.getenv("GMAIL_CLIENT_SECRET"))


def get_gmail_status(app: Flask) -> dict[str, Any]:
    """Return the current Gmail connection and sync status."""
    connection = connect_db(app)
    try:
        row = connection.execute("SELECT * FROM gmail_connections WHERE id = 1").fetchone()
        configured = gmail_is_configured()
        if row is None:
            interval = int(os.getenv("GMAIL_SYNC_INTERVAL_MINUTES", GMAIL_SYNC_INTERVAL_MINUTES))
            return {
                "connected": False,
                "oauth_configured": configured,
                "connected_email": None,
                "connected_at": None,
                "last_sync_at": None,
                "last_sync_error": None,
                "sync_interval_minutes": interval,
                "next_sync_at": None,
            }

        sync_interval_minutes = int(row["sync_interval_minutes"] or GMAIL_SYNC_INTERVAL_MINUTES)
        reference_time = row["last_sync_at"] or row["connected_at"]
        next_sync_at = _add_minutes(reference_time, sync_interval_minutes)
        return {
            "connected": True,
            "oauth_configured": configured,
            "connected_email": row["connected_email"],
            "connected_at": row["connected_at"],
            "last_sync_at": row["last_sync_at"],
            "last_sync_error": row["last_sync_error"],
            "sync_interval_minutes": sync_interval_minutes,
            "next_sync_at": next_sync_at,
        }
    finally:
        connection.close()


def start_gmail_authorization() -> tuple[str, str, str | None]:
    """Start the Gmail OAuth authorization flow."""
    if Flow is None:
        raise ValueError("Gmail OAuth dependencies are not installed.")
    client_config = _client_config()
    if client_config is None:
        raise ValueError("Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to connect Gmail.")

    flow = Flow.from_client_config(client_config, scopes=GMAIL_SCOPES)
    flow.redirect_uri = os.getenv("GMAIL_REDIRECT_URI", GMAIL_REDIRECT_URI)
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return authorization_url, state, getattr(flow, "code_verifier", None)


def finish_gmail_authorization(
    app: Flask,
    authorization_response: str,
    state: str | None,
    code_verifier: str | None,
) -> dict[str, Any]:
    """Complete the Gmail OAuth authorization flow and store tokens."""
    if Flow is None or Credentials is None:
        return {"ok": False, "error": "Gmail OAuth dependencies are not installed."}

    client_config = _client_config()
    if client_config is None:
        return {"ok": False, "error": "Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to connect Gmail."}
    if not state:
        return {"ok": False, "error": "Missing Gmail OAuth state."}

    flow = Flow.from_client_config(client_config, scopes=GMAIL_SCOPES, state=state)
    flow.redirect_uri = os.getenv("GMAIL_REDIRECT_URI", GMAIL_REDIRECT_URI)
    if code_verifier:
        flow.code_verifier = code_verifier

    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Exception as exc:
        return {"ok": False, "error": f"Gmail authorization failed: {exc}"}

    credentials = flow.credentials
    profile_email = _fetch_profile_email(credentials)

    connection = connect_db(app)
    try:
        _save_gmail_connection(
            connection,
            credentials,
            connected_email=profile_email,
            connected_at=utc_now(),
            last_sync_error=None,
        )
        connection.commit()
    finally:
        connection.close()

    return {"ok": True, "email": profile_email}


def disconnect_gmail(app: Flask) -> None:
    """Remove stored Gmail credentials and sync state."""
    connection = connect_db(app)
    try:
        connection.execute("DELETE FROM gmail_connections WHERE id = 1")
        connection.commit()
    finally:
        connection.close()


def sync_gmail_messages(app: Flask) -> dict[str, Any]:
    """Fetch new Gmail messages and convert them into applications."""
    connection = connect_db(app)
    try:
        row = connection.execute("SELECT * FROM gmail_connections WHERE id = 1").fetchone()
        if row is None:
            return {
                "ok": False,
                "error": "Connect Gmail before syncing.",
                "created": 0,
                "updated": 0,
                "skipped": 0,
            }

        credentials = _credentials_from_row(row)
        if credentials is None:
            return {
                "ok": False,
                "error": "Gmail OAuth dependencies are not installed.",
                "created": 0,
                "updated": 0,
                "skipped": 0,
            }

        refresh_error = _refresh_credentials_if_needed(connection, credentials)
        if refresh_error:
            connection.execute(
                "UPDATE gmail_connections SET last_sync_error = ?, updated_at = ? WHERE id = 1",
                (refresh_error, utc_now()),
            )
            connection.commit()
            return {
                "ok": False,
                "error": refresh_error,
                "created": 0,
                "updated": 0,
                "skipped": 0,
            }

        service = _build_gmail_service(credentials)
        if service is None:
            return {
                "ok": False,
                "error": "Gmail API dependencies are not installed.",
                "created": 0,
                "updated": 0,
                "skipped": 0,
            }

        sync_interval_minutes = int(row["sync_interval_minutes"] or GMAIL_SYNC_INTERVAL_MINUTES)
        since_reference = row["last_sync_at"] or row["connected_at"]
        since_epoch = _reference_epoch(since_reference, sync_interval_minutes)
        query = f"in:anywhere after:{since_epoch}"
        now = utc_now()

        created = 0
        updated = 0
        skipped = 0
        latest_error: str | None = None

        for message_id in _list_message_ids(service, query):
            try:
                message = _get_message(service, message_id)
                text = _message_to_text(message)
                parsed = parse_job_email(
                    text,
                    provider=os.getenv("AI_PROVIDER", "gemini").strip(),
                    gemini_model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip(),
                )

                if not parsed.get("is_job_related"):
                    skipped += 1
                    continue

                company = clean_string(parsed.get("company"))
                role = clean_string(parsed.get("role"))
                if not company or not role:
                    skipped += 1
                    continue

                company = clean_company(company)
                existing = fetch_application_by_gmail_message_id(connection, message_id)
                if existing is None:
                    existing = find_fuzzy_application(connection, company, role)

                status = _choose_status(existing.get("status") if existing else None, parsed.get("status"))
                applied_date = _message_date(message)
                notes = _gmail_notes(message, parsed)
                payload = {
                    "company": company,
                    "role": role,
                    "job_url": existing.get("job_url") if existing else None,
                    "source": existing.get("source") if existing else "Other",
                    "status": status,
                    "applied_date": existing.get("applied_date") if existing else applied_date,
                    "salary_currency": existing.get("salary_currency") if existing else "PHP",
                    "notes": _merge_notes(existing.get("notes") if existing else None, notes),
                    "follow_up_date": existing.get("follow_up_date") if existing else None,
                    "salary_min": existing.get("salary_min") if existing else None,
                    "salary_max": existing.get("salary_max") if existing else None,
                    "source_type": "gmail",
                    "gmail_message_id": existing.get("gmail_message_id") if existing else message_id,
                }

                if existing is None:
                    insert_application(connection, payload, now)
                    created += 1
                else:
                    update_application(connection, existing["id"], payload, partial=True)
                    updated += 1
            except Exception as exc:  # pragma: no cover - defensive sync guard
                latest_error = str(exc)
                skipped += 1
                continue

        connection.execute(
            """
            UPDATE gmail_connections
            SET last_sync_at = ?, last_sync_error = ?, credentials_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (now, latest_error, credentials.to_json(), now),
        )
        connection.commit()
        return {
            "ok": True,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "error": latest_error,
            "last_sync_at": now,
        }
    finally:
        connection.close()


def start_gmail_polling(app: Flask) -> None:
    """Start a lightweight background polling loop for Gmail sync."""
    global _POLL_STARTED
    with _POLL_LOCK:
        if _POLL_STARTED:
            return
        _POLL_STARTED = True

    if os.getenv("GMAIL_AUTO_POLL", "true").lower() not in {"1", "true", "yes"}:
        return

    thread = threading.Thread(target=_gmail_poll_loop, args=(app,), daemon=True)
    thread.start()


def _gmail_poll_loop(app: Flask) -> None:
    """Background loop that polls Gmail when the sync interval has elapsed."""
    while True:
        try:
            status = get_gmail_status(app)
            if status["connected"]:
                reference_time = status["last_sync_at"] or status["connected_at"]
                due_at = _parse_iso_time(reference_time) + timedelta(minutes=status["sync_interval_minutes"])
                if datetime.now(timezone.utc) >= due_at:
                    sync_gmail_messages(app)
        except Exception:
            pass
        time.sleep(GMAIL_POLL_INTERVAL_SECONDS)


def _client_config() -> dict[str, Any] | None:
    client_id = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    redirect_uri = os.getenv("GMAIL_REDIRECT_URI", GMAIL_REDIRECT_URI)
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _save_gmail_connection(
    connection,
    credentials,
    *,
    connected_email: str | None,
    connected_at: str,
    last_sync_error: str | None,
) -> None:
    timestamp = utc_now()
    existing = connection.execute("SELECT created_at, connected_at, last_sync_at FROM gmail_connections WHERE id = 1").fetchone()
    created_at = existing["created_at"] if existing else timestamp
    connected_at_value = connected_at or (existing["connected_at"] if existing else timestamp)
    last_sync_at = existing["last_sync_at"] if existing else None

    connection.execute(
        """
        INSERT INTO gmail_connections (
            id, credentials_json, connected_email, connected_at, last_sync_at,
            last_sync_error, sync_interval_minutes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            credentials_json = excluded.credentials_json,
            connected_email = excluded.connected_email,
            connected_at = excluded.connected_at,
            last_sync_at = excluded.last_sync_at,
            last_sync_error = excluded.last_sync_error,
            sync_interval_minutes = excluded.sync_interval_minutes,
            updated_at = excluded.updated_at
        """,
        (
            1,
            credentials.to_json(),
            connected_email,
            connected_at_value,
            last_sync_at,
            last_sync_error,
            int(os.getenv("GMAIL_SYNC_INTERVAL_MINUTES", GMAIL_SYNC_INTERVAL_MINUTES)),
            created_at,
            timestamp,
        ),
    )


def _refresh_credentials_if_needed(connection, credentials) -> str | None:
    if Request is None:
        return "Gmail OAuth dependencies are not installed."
    if getattr(credentials, "valid", False) and not getattr(credentials, "expired", False):
        return None
    if not getattr(credentials, "refresh_token", None):
        return None

    try:
        credentials.refresh(Request())
    except Exception as exc:
        return f"Gmail token refresh failed: {exc}"

    connection.execute(
        "UPDATE gmail_connections SET credentials_json = ?, updated_at = ? WHERE id = 1",
        (credentials.to_json(), utc_now()),
    )
    connection.commit()
    return None


def _credentials_from_row(row):
    if Credentials is None:
        return None
    info = json.loads(row["credentials_json"])
    return Credentials.from_authorized_user_info(info, scopes=GMAIL_SCOPES)


def _build_gmail_service(credentials):
    if build is None:
        return None
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _fetch_profile_email(credentials) -> str | None:
    service = _build_gmail_service(credentials)
    if service is None:
        return None
    try:
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:
        return None


def _list_message_ids(service, query: str) -> list[str]:
    message_ids: list[str] = []
    page_token: str | None = None

    while True:
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=100,
            pageToken=page_token,
        ).execute()
        message_ids.extend(
            [item["id"] for item in response.get("messages", []) if item.get("id")]
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids


def _get_message(service, message_id: str) -> dict[str, Any]:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def _message_to_text(message: dict[str, Any]) -> str:
    headers = {
        item.get("name", "").lower(): item.get("value", "")
        for item in message.get("payload", {}).get("headers", [])
    }
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    received_at = headers.get("date", "")
    body = _extract_text(message.get("payload", {}))
    snippet = message.get("snippet", "")

    segments = [
        f"Subject: {subject}" if subject else "",
        f"From: {sender}" if sender else "",
        f"Date: {received_at}" if received_at else "",
        body or snippet,
    ]
    return "\n".join(segment for segment in segments if segment).strip()


def _extract_text(part: dict[str, Any]) -> str:
    mime_type = part.get("mimeType", "")
    body = part.get("body", {}) or {}
    data = body.get("data")
    if mime_type == "text/plain" and data:
        return _decode_base64url(data)
    if mime_type == "text/html" and data:
        return _strip_html(_decode_base64url(data))

    for child in part.get("parts", []) or []:
        text = _extract_text(child)
        if text:
            return text
    return ""


def _decode_base64url(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="ignore")


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", " ", text)


def _message_date(message: dict[str, Any]) -> str:
    internal_date = message.get("internalDate")
    if internal_date:
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _reference_epoch(reference_time: str | None, sync_interval_minutes: int) -> int:
    if not reference_time:
        return int((datetime.now(timezone.utc) - timedelta(minutes=sync_interval_minutes)).timestamp())
    parsed = _parse_iso_time(reference_time)
    return int((parsed - timedelta(seconds=60)).timestamp())


def _parse_iso_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _add_minutes(value: str | None, minutes: int) -> str | None:
    if not value:
        return None
    return (_parse_iso_time(value) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _choose_status(existing_status: str | None, parsed_status: str | None) -> str:
    if not parsed_status:
        return existing_status or "Applied"
    if not existing_status:
        return parsed_status

    priorities = {
        "Applied": 0,
        "Interview Scheduled": 1,
        "Technical Test": 2,
        "Final Interview": 3,
        "Offer Received": 4,
        "Rejected": 5,
        "Ghosted": 6,
    }
    return parsed_status if priorities.get(parsed_status, 0) >= priorities.get(existing_status, 0) else existing_status


def _merge_notes(existing_notes: str | None, new_notes: str | None) -> str | None:
    notes = [note for note in [existing_notes, new_notes] if note]
    if not notes:
        return None
    if len(notes) == 1:
        return notes[0]
    if new_notes and new_notes in (existing_notes or ""):
        return existing_notes
    return "\n\n".join(notes)


def _gmail_notes(message: dict[str, Any], parsed: dict[str, Any]) -> str:
    subject = _header_value(message, "subject")
    note_parts = ["Gmail sync"]
    if subject:
        note_parts.append(subject)
    status = parsed.get("status")
    if status:
        note_parts.append(f"Parsed status: {status}")
    return " | ".join(note_parts)


def _header_value(message: dict[str, Any], header_name: str) -> str | None:
    for item in message.get("payload", {}).get("headers", []):
        if item.get("name", "").lower() == header_name.lower():
            return item.get("value")
    return None
