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
    DEFAULT_GROQ_MODEL,
    DEFAULT_PARSER_PROVIDER,
    GMAIL_POLL_INTERVAL_SECONDS,
    GMAIL_REDIRECT_URI,
    GMAIL_SCOPES,
    GMAIL_SYNC_INTERVAL_MINUTES,
)
from app.database import connect_db
from app.email_parser import parse_job_email_strict
from app.models import (
    fetch_application_by_id,
    find_fuzzy_application,
    update_application,
)
from app.parsed_emails import (
    fetch_emails_needing_parse,
    update_parse_failure,
    update_parse_success,
    upsert_email_record,
)
from app.utils import clean_company, clean_string, utc_now
from app.watchers import match_application_by_sender

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


def get_gmail_status(app: Flask, user_id: int) -> dict[str, Any]:
    """Return the current Gmail connection and sync status for a user."""
    connection = connect_db(app)
    try:
        row = connection.execute(
            "SELECT * FROM gmail_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
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
    user_id: int,
) -> dict[str, Any]:
    """Complete the Gmail OAuth authorization flow and store tokens for a user."""
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
        _save_gmail_token(
            connection,
            credentials,
            user_id=user_id,
            connected_email=profile_email,
            connected_at=utc_now(),
            last_sync_error=None,
        )
        connection.commit()
    finally:
        connection.close()

    return {"ok": True, "email": profile_email}


def disconnect_gmail(app: Flask, user_id: int) -> None:
    """Remove stored Gmail credentials for a user."""
    connection = connect_db(app)
    try:
        connection.execute("DELETE FROM gmail_tokens WHERE user_id = ?", (user_id,))
        connection.commit()
    finally:
        connection.close()


def sync_gmail_messages(
    app: Flask,
    *,
    user_id: int,
    parser_provider: str | None = None,
    gemini_model: str | None = None,
    groq_model: str | None = None,
) -> dict[str, Any]:
    """Fetch new Gmail messages and convert them into applications for a user."""
    connection = connect_db(app)
    try:
        row = connection.execute(
            "SELECT * FROM gmail_tokens WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "Connect Gmail before syncing.", "created": 0, "updated": 0, "skipped": 0}

        credentials = _credentials_from_row(row)
        if credentials is None:
            return {"ok": False, "error": "Gmail OAuth dependencies are not installed.", "created": 0, "updated": 0, "skipped": 0}

        refresh_error = _refresh_credentials_if_needed(connection, credentials, user_id)
        if refresh_error:
            connection.execute(
                "UPDATE gmail_tokens SET last_sync_error = ?, updated_at = ? WHERE user_id = ?",
                (refresh_error, utc_now(), user_id),
            )
            connection.commit()
            return {"ok": False, "error": refresh_error, "created": 0, "updated": 0, "skipped": 0}

        service = _build_gmail_service(credentials)
        if service is None:
            return {"ok": False, "error": "Gmail API dependencies are not installed.", "created": 0, "updated": 0, "skipped": 0}

        sync_interval_minutes = int(row["sync_interval_minutes"] or GMAIL_SYNC_INTERVAL_MINUTES)
        since_reference = row["last_sync_at"] or row["connected_at"]
        since_epoch = _reference_epoch(since_reference, sync_interval_minutes)
        gmail_query = f"in:anywhere after:{since_epoch}"
        now = utc_now()

        fetched = 0
        latest_error: str | None = None

        for message_id in _list_message_ids(service, gmail_query):
            try:
                message = _get_message(service, message_id)
                inserted = upsert_email_record(
                    connection,
                    gmail_message_id=message_id,
                    received_at=_message_received_at(message),
                    from_address=_header_value(message, "from"),
                    subject=_header_value(message, "subject"),
                    body_text=_message_to_text(message),
                    user_id=user_id,
                )
                if inserted:
                    fetched += 1
            except Exception as exc:  # pragma: no cover
                latest_error = str(exc)
                continue

        connection.commit()

        _provider = (parser_provider or os.getenv("PARSER_PROVIDER") or DEFAULT_PARSER_PROVIDER).strip().lower()
        _gemini = (gemini_model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()
        _groq = (groq_model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL).strip()

        parsed_count, updated, pending_review, paused, not_job, parse_error, auto_updated_names = _parse_pending_emails(
            connection, user_id=user_id, parser_provider=_provider, gemini_model=_gemini, groq_model=_groq
        )
        if parse_error:
            latest_error = parse_error

        connection.commit()

        connection.execute(
            """
            UPDATE gmail_tokens
            SET last_sync_at = ?, last_sync_error = ?, credentials_json = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (now, latest_error, credentials.to_json(), now, user_id),
        )
        connection.commit()
        return {
            "ok": True,
            "fetched": fetched,
            "parsed": parsed_count,
            "updated": updated,
            "pending_review": pending_review,
            "paused": paused,
            "not_job": not_job,
            "created": 0,
            "skipped": not_job + paused,
            "error": latest_error,
            "last_sync_at": now,
            "auto_updated_names": auto_updated_names,
        }
    finally:
        connection.close()


def retry_parse_email(
    app: Flask,
    gmail_message_id: str,
    *,
    user_id: int,
    parser_provider: str | None = None,
    gemini_model: str | None = None,
    groq_model: str | None = None,
) -> dict[str, Any]:
    """Force a single email back into the parse queue and parse it immediately.

    Returns a dict describing the outcome: ``{ok, parse_status, error}``.
    """
    from app.parsed_emails import fetch_email, mark_for_retry

    connection = connect_db(app)
    try:
        record = fetch_email(connection, gmail_message_id)
        if record is None:
            return {"ok": False, "error": "Email not found.", "parse_status": None}

        mark_for_retry(connection, gmail_message_id)
        connection.commit()

        _provider = (parser_provider or os.getenv("PARSER_PROVIDER") or DEFAULT_PARSER_PROVIDER).strip().lower()
        _gemini = (gemini_model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()
        _groq = (groq_model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL).strip()
        _parse_pending_emails(connection, user_id=user_id, parser_provider=_provider, gemini_model=_gemini, groq_model=_groq)
        connection.commit()

        updated = fetch_email(connection, gmail_message_id) or {}
        return {
            "ok": True,
            "parse_status": updated.get("parse_status"),
            "error": updated.get("parse_error"),
        }
    finally:
        connection.close()


def _parse_pending_emails(
    connection,
    *,
    user_id: int,
    parser_provider: str,
    gemini_model: str,
    groq_model: str,
) -> tuple[int, int, int, int, int, str | None, list[str]]:
    """Parse every paused email row.

    Returns ``(parsed, auto_updated, pending_review, paused, not_job, latest_error)``.
    """
    parsed_count = 0
    auto_updated = 0
    pending_review = 0
    still_paused = 0
    not_job = 0
    latest_error: str | None = None
    auto_updated_names: list[str] = []

    for record in fetch_emails_needing_parse(connection, user_id):
        message_id = record["gmail_message_id"]
        body = record.get("body_text") or ""
        sender = record.get("from_address") or ""

        result, error = parse_job_email_strict(
            body,
            provider=parser_provider,
            gemini_model=gemini_model,
            groq_model=groq_model,
        )
        if result is None:
            update_parse_failure(connection, message_id, error or "Parse failed.")
            still_paused += 1
            latest_error = error or latest_error
            continue

        parsed_count += 1
        is_job = bool(result.get("is_job_related"))
        company = clean_string(result.get("company"))
        role = clean_string(result.get("role"))
        parsed_status_value = result.get("status")
        confidence = result.get("confidence")
        reasoning = result.get("reasoning_summary")

        if not is_job:
            update_parse_success(
                connection,
                message_id,
                parse_status="not_job",
                is_job_related=False,
                parsed_company=company,
                parsed_role=role,
                parsed_status=parsed_status_value,
                parsed_confidence=confidence,
                parsed_reasoning=reasoning,
            )
            not_job += 1
            continue

        # Job-related: try to route to an existing application.
        target_id = match_application_by_sender(connection, sender, user_id=user_id)
        target = fetch_application_by_id(connection, target_id) if target_id else None

        if target is None and company and role:
            cleaned = clean_company(company)
            target = find_fuzzy_application(connection, cleaned, role, user_id=user_id)

        if target is not None:
            new_status = _choose_status(target.get("status"), parsed_status_value)
            notes = _email_notes(record.get("subject"), parsed_status_value)
            payload = {
                "company": target["company"],
                "role": target["role"],
                "status": new_status,
                "notes": _merge_notes(target.get("notes"), notes),
                "source_type": "gmail",
                "gmail_message_id": target.get("gmail_message_id") or message_id,
            }
            update_application(connection, target["id"], payload, partial=True)
            update_parse_success(
                connection,
                message_id,
                parse_status="auto_updated",
                is_job_related=True,
                parsed_company=company,
                parsed_role=role,
                parsed_status=parsed_status_value,
                parsed_confidence=confidence,
                parsed_reasoning=reasoning,
                application_id=target["id"],
            )
            auto_updated += 1
            auto_updated_names.append(f"{target['company']} — {target['role']} → {new_status}")
            continue

        # No match: queue for user review.
        update_parse_success(
            connection,
            message_id,
            parse_status="pending_review",
            is_job_related=True,
            parsed_company=company,
            parsed_role=role,
            parsed_status=parsed_status_value,
            parsed_confidence=confidence,
            parsed_reasoning=reasoning,
        )
        pending_review += 1

    return parsed_count, auto_updated, pending_review, still_paused, not_job, latest_error, auto_updated_names


def _email_notes(subject: str | None, parsed_status: str | None) -> str:
    parts = ["Gmail sync"]
    if subject:
        parts.append(subject)
    if parsed_status:
        parts.append(f"Parsed status: {parsed_status}")
    return " | ".join(parts)


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
    """Background loop that polls Gmail for all connected users."""
    while True:
        try:
            connection = connect_db(app)
            try:
                rows = connection.execute("SELECT user_id FROM gmail_tokens").fetchall()
                user_ids = [row["user_id"] for row in rows]
            finally:
                connection.close()

            for uid in user_ids:
                try:
                    status = get_gmail_status(app, uid)
                    if status["connected"]:
                        reference_time = status["last_sync_at"] or status["connected_at"]
                        due_at = _parse_iso_time(reference_time) + timedelta(minutes=status["sync_interval_minutes"])
                        if datetime.now(timezone.utc) >= due_at:
                            sync_gmail_messages(app, user_id=uid)
                except Exception:
                    pass
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


def _save_gmail_token(
    connection,
    credentials,
    *,
    user_id: int,
    connected_email: str | None,
    connected_at: str,
    last_sync_error: str | None,
) -> None:
    timestamp = utc_now()
    existing = connection.execute(
        "SELECT created_at, connected_at, last_sync_at FROM gmail_tokens WHERE user_id = ?", (user_id,)
    ).fetchone()
    created_at = existing["created_at"] if existing else timestamp
    connected_at_value = connected_at or (existing["connected_at"] if existing else timestamp)
    last_sync_at = existing["last_sync_at"] if existing else None

    connection.execute(
        """
        INSERT INTO gmail_tokens (
            user_id, credentials_json, connected_email, connected_at, last_sync_at,
            last_sync_error, sync_interval_minutes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            credentials_json = excluded.credentials_json,
            connected_email = excluded.connected_email,
            connected_at = excluded.connected_at,
            last_sync_at = excluded.last_sync_at,
            last_sync_error = excluded.last_sync_error,
            sync_interval_minutes = excluded.sync_interval_minutes,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
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


def _refresh_credentials_if_needed(connection, credentials, user_id: int) -> str | None:
    if Request is None:
        return "Gmail OAuth dependencies are not installed."
    if getattr(credentials, "valid", False) and not getattr(credentials, "expired", False):
        return None
    if not getattr(credentials, "refresh_token", None):
        return "Gmail token has expired and cannot be refreshed — please reconnect Gmail."

    try:
        credentials.refresh(Request())
    except Exception as exc:
        return f"Gmail token refresh failed: {exc}"

    connection.execute(
        "UPDATE gmail_tokens SET credentials_json = ?, updated_at = ? WHERE user_id = ?",
        (credentials.to_json(), utc_now(), user_id),
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


def _message_received_at(message: dict[str, Any]) -> str | None:
    """Return the message timestamp as ISO 8601 UTC, or None if unavailable."""
    internal_date = message.get("internalDate")
    if not internal_date:
        return None
    try:
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


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


def _header_value(message: dict[str, Any], header_name: str) -> str | None:
    for item in message.get("payload", {}).get("headers", []):
        if item.get("name", "").lower() == header_name.lower():
            return item.get("value")
    return None
