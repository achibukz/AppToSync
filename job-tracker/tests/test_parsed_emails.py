"""Tests for the parsed-emails review queue feature.

Covers:
- parsed_emails CRUD primitives
- Gmail sync stores fetched messages and parses them with strict (Gemini-only) parser
- Watcher routing → auto-update existing application silently
- Fuzzy match routing → auto-update existing application silently
- New, unmatched, job-related email → pending_review queue
- Not-job emails → not_job state, hidden from review queue
- Gemini failure → paused, retried automatically on next sync
- Manual retry route forces immediate re-parse
- Accept route creates application and links the email
- Reject route marks dismissed (and never resurfaces)
- Dedup: a second sync of the same message ID does not double-process
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.database import connect_db
from app.gmail import sync_gmail_messages, retry_parse_email
from app.parsed_emails import (
    PARSE_STATUSES,
    fetch_email,
    fetch_emails_needing_parse,
    fetch_paused,
    fetch_pending_review,
    mark_accepted,
    mark_dismissed,
    update_parse_failure,
    update_parse_success,
    upsert_email_record,
)
from app.utils import utc_now
from app.watchers import set_watchers_for_application


FAKE_CREDENTIALS_JSON = (
    '{"token":"x","refresh_token":"y","token_uri":"https://oauth2.googleapis.com/token",'
    '"client_id":"cid","client_secret":"sec","scopes":["https://www.googleapis.com/auth/gmail.readonly"]}'
)


FAKE_MESSAGES = {
    "msg-google": {
        "internalDate": "1745900000000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Interview for Software Engineer at Google"},
                {"name": "From", "value": "no-reply@greenhouse.io"},
            ]
        },
        "snippet": "We would like to schedule an interview for the Software Engineer role at Google.",
    },
    "msg-stripe": {
        "internalDate": "1745900100000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Application received — Backend Engineer"},
                {"name": "From", "value": "Stripe Recruiting <recruiting@stripe.com>"},
            ]
        },
        "snippet": "Thanks for applying to the Backend Engineer role at Stripe.",
    },
    "msg-newjob": {
        "internalDate": "1745900200000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Thanks for applying to Product Manager at NewCo"},
                {"name": "From", "value": "hr@newco.io"},
            ]
        },
        "snippet": "Thanks for applying to Product Manager at NewCo.",
    },
    "msg-not-job": {
        "internalDate": "1745900300000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Your invoice"},
                {"name": "From", "value": "billing@somesite.com"},
            ]
        },
        "snippet": "Your monthly invoice is ready.",
    },
}


PARSED_RESULTS = {
    "msg-google": {
        "is_job_related": True,
        "company": "Google",
        "role": "Software Engineer",
        "status": "Interview Scheduled",
        "interview_date": None,
        "confidence": 0.92,
        "extracted_by": "gemini",
        "reasoning_summary": "Interview invitation.",
        "field_explanations": {},
    },
    "msg-stripe": {
        "is_job_related": True,
        "company": "Stripe",
        "role": "Backend Engineer",
        "status": "Applied",
        "interview_date": None,
        "confidence": 0.9,
        "extracted_by": "gemini",
        "reasoning_summary": "Application received.",
        "field_explanations": {},
    },
    "msg-newjob": {
        "is_job_related": True,
        "company": "NewCo",
        "role": "Product Manager",
        "status": "Applied",
        "interview_date": None,
        "confidence": 0.85,
        "extracted_by": "gemini",
        "reasoning_summary": "Application acknowledged.",
        "field_explanations": {},
    },
    "msg-not-job": {
        "is_job_related": False,
        "company": None,
        "role": None,
        "status": None,
        "interview_date": None,
        "confidence": 0.1,
        "extracted_by": "gemini",
        "reasoning_summary": "Looks like a billing email.",
        "field_explanations": {},
    },
}


class FakeCredentials:
    valid = True
    expired = False
    refresh_token = "y"

    def to_json(self) -> str:
        return '{"token":"x","refresh_token":"y"}'


def _make_strict_parser(result_overrides: dict[str, tuple[dict | None, str | None]] | None = None):
    """Return a fake parse_job_email_strict that uses PARSED_RESULTS by default.

    `result_overrides` lets a test customise the (result, error) tuple per snippet,
    e.g. to force a Gemini error for a specific message body.
    """
    overrides = result_overrides or {}

    def _parse(text: str, gemini_model: str | None = None):
        for mid, msg in FAKE_MESSAGES.items():
            snippet = msg.get("snippet", "")
            if snippet and snippet in text:
                if mid in overrides:
                    return overrides[mid]
                return PARSED_RESULTS[mid], None
        return {"is_job_related": False, "company": None, "role": None, "status": None,
                "interview_date": None, "confidence": 0.1, "extracted_by": "gemini",
                "reasoning_summary": None, "field_explanations": {}}, None

    return _parse


class ParsedEmailTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app({
            "TESTING": True,
            "DATABASE_PATH": self.database_path,
            "SEED_DEMO_DATA": False,
        })

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _conn(self):
        return connect_db(self.app)

    def _seed_gmail_connection(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO gmail_connections (
                    id, credentials_json, connected_email, connected_at, last_sync_at,
                    last_sync_error, sync_interval_minutes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, FAKE_CREDENTIALS_JSON, "user@example.com",
                 utc_now(), None, None, 15, utc_now(), utc_now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _create_application(self, company: str, role: str, status: str = "Applied") -> str:
        from app.models import insert_application
        conn = self._conn()
        try:
            app = insert_application(conn, {
                "company": company,
                "role": role,
                "source": "Other",
                "status": status,
                "applied_date": "2026-04-01",
                "source_type": "manual",
            }, utc_now())
            conn.commit()
            return app["id"]
        finally:
            conn.close()

    def _run_sync(self, message_ids: list[str], parser=None) -> dict:
        if parser is None:
            parser = _make_strict_parser()
        messages = {mid: FAKE_MESSAGES[mid] for mid in message_ids}
        with (
            patch("app.gmail._credentials_from_row", return_value=FakeCredentials()),
            patch("app.gmail._refresh_credentials_if_needed", return_value=None),
            patch("app.gmail._build_gmail_service", return_value=object()),
            patch("app.gmail._list_message_ids", return_value=message_ids),
            patch("app.gmail._get_message", side_effect=lambda _s, mid: messages[mid]),
            patch("app.gmail.parse_job_email_strict", side_effect=parser),
        ):
            return sync_gmail_messages(self.app)


# ---------------------------------------------------------------------------
# CRUD primitives
# ---------------------------------------------------------------------------

class TestParsedEmailsCRUD(ParsedEmailTestBase):
    def test_upsert_inserts_new_row(self) -> None:
        conn = self._conn()
        try:
            inserted = upsert_email_record(
                conn,
                gmail_message_id="m1",
                received_at="2026-04-01T10:00:00+00:00",
                from_address="hr@example.com",
                subject="Welcome",
                body_text="Hi",
            )
            conn.commit()
            self.assertTrue(inserted)
            row = fetch_email(conn, "m1")
            self.assertEqual(row["parse_status"], "paused")
            self.assertEqual(row["parse_attempts"], 0)
            self.assertEqual(row["from_address"], "hr@example.com")
        finally:
            conn.close()

    def test_upsert_ignores_duplicates(self) -> None:
        conn = self._conn()
        try:
            self.assertTrue(upsert_email_record(conn, gmail_message_id="m1",
                                                received_at=None, from_address=None,
                                                subject=None, body_text=None))
            self.assertFalse(upsert_email_record(conn, gmail_message_id="m1",
                                                 received_at=None, from_address=None,
                                                 subject="Different", body_text=None))
            conn.commit()
            row = fetch_email(conn, "m1")
            # Original row should be preserved.
            self.assertIsNone(row["subject"])
        finally:
            conn.close()

    def test_update_parse_failure_keeps_status_paused(self) -> None:
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="m1", received_at=None,
                                from_address=None, subject=None, body_text="body")
            update_parse_failure(conn, "m1", "Gemini quota exceeded")
            conn.commit()
            row = fetch_email(conn, "m1")
            self.assertEqual(row["parse_status"], "paused")
            self.assertEqual(row["parse_error"], "Gemini quota exceeded")
            self.assertEqual(row["parse_attempts"], 1)
        finally:
            conn.close()

    def test_update_parse_success_records_routing(self) -> None:
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="m1", received_at=None,
                                from_address=None, subject=None, body_text="body")
            update_parse_success(
                conn, "m1",
                parse_status="pending_review",
                is_job_related=True,
                parsed_company="Acme",
                parsed_role="SWE",
                parsed_status="Applied",
                parsed_confidence=0.9,
                parsed_reasoning="thanks for applying",
            )
            conn.commit()
            row = fetch_email(conn, "m1")
            self.assertEqual(row["parse_status"], "pending_review")
            self.assertEqual(row["is_job_related"], 1)
            self.assertEqual(row["parsed_company"], "Acme")
            self.assertEqual(row["parsed_confidence"], 0.9)
            self.assertEqual(row["parse_attempts"], 1)
            self.assertIsNone(row["parse_error"])
        finally:
            conn.close()

    def test_mark_dismissed(self) -> None:
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="m1", received_at=None,
                                from_address=None, subject=None, body_text="body")
            mark_dismissed(conn, "m1")
            conn.commit()
            self.assertEqual(fetch_email(conn, "m1")["parse_status"], "dismissed")
        finally:
            conn.close()

    def test_mark_accepted_links_application(self) -> None:
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="m1", received_at=None,
                                from_address=None, subject=None, body_text="body")
            mark_accepted(conn, "m1", "app-abc")
            conn.commit()
            row = fetch_email(conn, "m1")
            self.assertEqual(row["parse_status"], "accepted")
            self.assertEqual(row["application_id"], "app-abc")
        finally:
            conn.close()

    def test_invalid_parse_status_rejected(self) -> None:
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="m1", received_at=None,
                                from_address=None, subject=None, body_text="body")
            with self.assertRaises(ValueError):
                update_parse_success(
                    conn, "m1",
                    parse_status="bogus",
                    is_job_related=True,
                    parsed_company=None, parsed_role=None,
                    parsed_status=None, parsed_confidence=None, parsed_reasoning=None,
                )
        finally:
            conn.close()

    def test_parse_statuses_constant_is_complete(self) -> None:
        # Sanity: all statuses the application code references should be valid.
        for status in {"paused", "pending_review", "auto_updated", "not_job", "accepted", "dismissed"}:
            self.assertIn(status, PARSE_STATUSES)


# ---------------------------------------------------------------------------
# Sync pipeline integration
# ---------------------------------------------------------------------------

class TestSyncPipeline(ParsedEmailTestBase):
    def test_sync_stores_fetched_messages_in_queue(self) -> None:
        self._seed_gmail_connection()
        result = self._run_sync(["msg-google", "msg-newjob"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["parsed"], 2)

        conn = self._conn()
        try:
            self.assertIsNotNone(fetch_email(conn, "msg-google"))
            self.assertIsNotNone(fetch_email(conn, "msg-newjob"))
        finally:
            conn.close()

    def test_new_unmatched_email_goes_to_pending_review(self) -> None:
        self._seed_gmail_connection()
        result = self._run_sync(["msg-newjob"])

        self.assertEqual(result["pending_review"], 1)
        self.assertEqual(result["updated"], 0)

        conn = self._conn()
        try:
            row = fetch_email(conn, "msg-newjob")
            self.assertEqual(row["parse_status"], "pending_review")
            self.assertEqual(row["parsed_company"], "NewCo")
            self.assertEqual(row["parsed_role"], "Product Manager")
            queue = fetch_pending_review(conn)
            self.assertEqual(len(queue), 1)
        finally:
            conn.close()

    def test_watcher_routes_to_existing_application_silently(self) -> None:
        self._seed_gmail_connection()
        app_id = self._create_application("Google", "Software Engineer", "Applied")
        conn = self._conn()
        try:
            set_watchers_for_application(conn, app_id, ["@greenhouse.io"])
            conn.commit()
        finally:
            conn.close()

        result = self._run_sync(["msg-google"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["pending_review"], 0)

        # Application updated.
        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(apps[0]["status"], "Interview Scheduled")
        self.assertEqual(apps[0]["company"], "Google")  # never overridden

        # Email row.
        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-google")
            self.assertEqual(email["parse_status"], "auto_updated")
            self.assertEqual(email["application_id"], app_id)
        finally:
            conn.close()

    def test_fuzzy_match_auto_updates_existing_application(self) -> None:
        self._seed_gmail_connection()
        app_id = self._create_application("Stripe", "Backend Engineer", "Applied")

        # No watcher → fuzzy match against parsed company+role.
        result = self._run_sync(["msg-stripe"])

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["pending_review"], 0)

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-stripe")
            self.assertEqual(email["parse_status"], "auto_updated")
            self.assertEqual(email["application_id"], app_id)
        finally:
            conn.close()

    def test_not_job_email_marked_not_job(self) -> None:
        self._seed_gmail_connection()
        result = self._run_sync(["msg-not-job"])

        self.assertEqual(result["pending_review"], 0)
        self.assertEqual(result["not_job"], 1)

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-not-job")
            self.assertEqual(email["parse_status"], "not_job")
            self.assertEqual(email["is_job_related"], 0)
            # Should not appear in either review queue.
            self.assertEqual(fetch_pending_review(conn), [])
            self.assertEqual(fetch_paused(conn), [])
        finally:
            conn.close()

    def test_gemini_failure_pauses_email_for_retry(self) -> None:
        self._seed_gmail_connection()
        parser = _make_strict_parser({"msg-newjob": (None, "Gemini quota exceeded")})

        result = self._run_sync(["msg-newjob"], parser)

        self.assertEqual(result["paused"], 1)
        self.assertEqual(result["pending_review"], 0)
        self.assertIn("quota", result["error"])

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-newjob")
            self.assertEqual(email["parse_status"], "paused")
            self.assertEqual(email["parse_attempts"], 1)
            self.assertIn("quota", email["parse_error"])
        finally:
            conn.close()

    def test_paused_email_retried_on_next_sync(self) -> None:
        """A second sync with a working parser should pick up the paused email and parse it."""
        self._seed_gmail_connection()

        # First sync: Gemini fails.
        failing = _make_strict_parser({"msg-newjob": (None, "Gemini timeout")})
        self._run_sync(["msg-newjob"], failing)

        conn = self._conn()
        try:
            self.assertEqual(fetch_email(conn, "msg-newjob")["parse_status"], "paused")
        finally:
            conn.close()

        # Second sync: Gemini works. Even though Gmail returns no new messages,
        # the paused email should still be re-parsed.
        result = self._run_sync([])
        self.assertEqual(result["pending_review"], 1)

        conn = self._conn()
        try:
            row = fetch_email(conn, "msg-newjob")
            self.assertEqual(row["parse_status"], "pending_review")
            self.assertEqual(row["parse_attempts"], 2)
        finally:
            conn.close()

    def test_dedup_second_sync_does_not_double_process(self) -> None:
        self._seed_gmail_connection()
        self._run_sync(["msg-newjob"])

        conn = self._conn()
        try:
            self.assertEqual(fetch_email(conn, "msg-newjob")["parse_status"], "pending_review")
        finally:
            conn.close()

        # Second sync with the same message id: row already exists, no re-parse.
        result = self._run_sync(["msg-newjob"])
        self.assertEqual(result["fetched"], 0)  # no new row inserted
        self.assertEqual(result["parsed"], 0)   # nothing was paused

    def test_no_emails_to_parse_returns_zero_counts(self) -> None:
        self._seed_gmail_connection()
        result = self._run_sync([])
        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched"], 0)
        self.assertEqual(result["parsed"], 0)


# ---------------------------------------------------------------------------
# Routes: accept / reject / retry
# ---------------------------------------------------------------------------

class TestEmailRoutes(ParsedEmailTestBase):
    def _seed_pending_review_email(self, message_id: str = "msg-newjob") -> None:
        """Helper: insert a parsed_emails row already in pending_review state."""
        conn = self._conn()
        try:
            upsert_email_record(
                conn,
                gmail_message_id=message_id,
                received_at="2026-04-01T10:00:00+00:00",
                from_address="hr@newco.io",
                subject="Thanks for applying to Product Manager at NewCo",
                body_text="Thanks for applying.",
            )
            update_parse_success(
                conn, message_id,
                parse_status="pending_review",
                is_job_related=True,
                parsed_company="NewCo",
                parsed_role="Product Manager",
                parsed_status="Applied",
                parsed_confidence=0.9,
                parsed_reasoning="Application acknowledged.",
            )
            conn.commit()
        finally:
            conn.close()

    def test_accept_one_click_creates_application_from_parsed_values(self) -> None:
        self._seed_pending_review_email()

        client = self.app.test_client()
        response = client.post("/emails/msg-newjob/accept", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        apps = client.get("/api/applications").get_json()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["company"], "NewCo")
        self.assertEqual(apps[0]["role"], "Product Manager")
        self.assertEqual(apps[0]["source_type"], "gmail")
        self.assertEqual(apps[0]["gmail_message_id"], "msg-newjob")

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-newjob")
            self.assertEqual(email["parse_status"], "accepted")
            self.assertEqual(email["application_id"], apps[0]["id"])
        finally:
            conn.close()

    def test_accept_with_form_data_overrides_parsed_values(self) -> None:
        self._seed_pending_review_email()

        client = self.app.test_client()
        client.post(
            "/emails/msg-newjob/accept",
            data={
                "company": "Override Inc",
                "role": "Senior PM",
                "status": "Interview Scheduled",
                "applied_date": "2026-04-15",
                "source": "LinkedIn",
            },
            follow_redirects=True,
        )

        apps = client.get("/api/applications").get_json()
        self.assertEqual(apps[0]["company"], "Override Inc")
        self.assertEqual(apps[0]["role"], "Senior PM")
        self.assertEqual(apps[0]["status"], "Interview Scheduled")

    def test_reject_marks_email_dismissed(self) -> None:
        self._seed_pending_review_email()

        client = self.app.test_client()
        response = client.post("/emails/msg-newjob/reject", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-newjob")
            self.assertEqual(email["parse_status"], "dismissed")
            # Should not appear in any review queue.
            self.assertEqual(fetch_pending_review(conn), [])
        finally:
            conn.close()

    def test_dismissed_email_not_resurrected_on_next_sync(self) -> None:
        """A rejected email must not come back if Gmail sync sees it again."""
        self._seed_gmail_connection()
        self._seed_pending_review_email()

        client = self.app.test_client()
        client.post("/emails/msg-newjob/reject", follow_redirects=True)

        # Run sync again with the same message id.
        self._run_sync(["msg-newjob"])

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-newjob")
            self.assertEqual(email["parse_status"], "dismissed")  # unchanged
        finally:
            conn.close()

    def test_retry_route_forces_reparse(self) -> None:
        self._seed_gmail_connection()
        # Seed a paused email with an error.
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="msg-newjob",
                                received_at="2026-04-01T10:00:00+00:00",
                                from_address="hr@newco.io",
                                subject="Thanks for applying to Product Manager at NewCo",
                                body_text="Thanks for applying to Product Manager at NewCo.")
            update_parse_failure(conn, "msg-newjob", "Gemini timeout")
            conn.commit()
        finally:
            conn.close()

        # Retry with a working parser.
        with patch("app.gmail.parse_job_email_strict", side_effect=_make_strict_parser()):
            result = retry_parse_email(self.app, "msg-newjob")

        self.assertTrue(result["ok"])
        self.assertEqual(result["parse_status"], "pending_review")

        conn = self._conn()
        try:
            email = fetch_email(conn, "msg-newjob")
            self.assertEqual(email["parse_status"], "pending_review")
            self.assertEqual(email["parsed_company"], "NewCo")
        finally:
            conn.close()

    def test_retry_route_pauses_again_on_repeat_failure(self) -> None:
        self._seed_gmail_connection()
        conn = self._conn()
        try:
            upsert_email_record(conn, gmail_message_id="msg-newjob",
                                received_at=None, from_address=None,
                                subject=None,
                                body_text="Thanks for applying to Product Manager at NewCo.")
            update_parse_failure(conn, "msg-newjob", "Gemini timeout")
            conn.commit()
        finally:
            conn.close()

        failing = _make_strict_parser({"msg-newjob": (None, "Gemini still down")})
        with patch("app.gmail.parse_job_email_strict", side_effect=failing):
            result = retry_parse_email(self.app, "msg-newjob")

        self.assertTrue(result["ok"])
        self.assertEqual(result["parse_status"], "paused")
        self.assertIn("Gemini still down", result["error"])

    def test_accept_route_rejects_already_accepted_email(self) -> None:
        self._seed_pending_review_email()
        client = self.app.test_client()
        client.post("/emails/msg-newjob/accept", follow_redirects=True)

        # Trying to accept again should be a no-op (no second app created).
        response = client.post("/emails/msg-newjob/accept", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        apps = client.get("/api/applications").get_json()
        self.assertEqual(len(apps), 1)

    def test_api_list_emails_returns_grouped(self) -> None:
        self._seed_pending_review_email()
        client = self.app.test_client()
        response = client.get("/api/emails")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(len(body["pending_review"]), 1)
        self.assertEqual(body["paused"], [])

    def test_api_get_email_returns_record(self) -> None:
        self._seed_pending_review_email()
        client = self.app.test_client()
        response = client.get("/api/emails/msg-newjob")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["parsed_company"], "NewCo")

    def test_api_get_email_404_for_unknown(self) -> None:
        client = self.app.test_client()
        response = client.get("/api/emails/nope")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
