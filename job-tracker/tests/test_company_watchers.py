"""Tests for application-scoped email watcher feature.

Covers:
- Watcher CRUD (set, fetch, delete)
- Sender pattern matching (domain, full address, display-name format)
- Gmail sync: watcher branch routes email to the matched application
- Gmail sync: parser still gates job-relevance (LinkedIn noise test)
- Gmail sync: fallback branch unchanged when no watcher matches
- gmail_message_id stamped on fuzzy-matched records (regression)
- Watcher saved and loaded through the update route
- Watcher cleaned up when application is deleted
- Expired-credentials bug regression
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.database import connect_db
from app.gmail import sync_gmail_messages
from app.utils import utc_now
from app.watchers import (
    delete_watchers_for_application,
    fetch_watchers_for_application,
    match_application_by_sender,
    set_watchers_for_application,
)


# ---------------------------------------------------------------------------
# Shared fake data
# ---------------------------------------------------------------------------

FAKE_CREDENTIALS_JSON = (
    '{"token":"tok","refresh_token":"ref","token_uri":"https://oauth2.googleapis.com/token",'
    '"client_id":"cid","client_secret":"sec","scopes":["https://www.googleapis.com/auth/gmail.readonly"]}'
)

# Fake Gmail messages keyed by message-id
FAKE_MESSAGES: dict[str, dict] = {
    "msg-greenhouse": {
        "internalDate": "1745900000000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Interview invitation — Software Engineer at Google"},
                {"name": "From", "value": "no-reply@greenhouse.io"},
            ]
        },
        "snippet": "We would like to schedule an interview for the Software Engineer role.",
    },
    "msg-linkedin": {
        "internalDate": "1745900100000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Your application was sent to Google"},
                {"name": "From", "value": "jobs-noreply@linkedin.com"},
            ]
        },
        "snippet": "Your application to Google was submitted. Good luck!",
    },
    "msg-not-job": {
        "internalDate": "1745900200000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Your LinkedIn premium invoice"},
                {"name": "From", "value": "billing@linkedin.com"},
            ]
        },
        "snippet": "Your invoice for LinkedIn Premium is ready.",
    },
    "msg-stripe": {
        "internalDate": "1745900300000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Application received — Backend Engineer"},
                {"name": "From", "value": "Stripe Recruiting <recruiting@stripe.com>"},
            ]
        },
        "snippet": "Thanks for applying to the Backend Engineer role at Stripe.",
    },
    "msg-unknown": {
        "internalDate": "1745900400000",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Thanks for applying to Product Manager"},
                {"name": "From", "value": "hr@randomco.io"},
            ]
        },
        "snippet": "Thanks for applying to Product Manager at RandomCo.",
    },
}


class FakeCredentials:
    valid = True
    expired = False
    refresh_token = "refresh"

    def to_json(self) -> str:
        return '{"token":"x","refresh_token":"y"}'


def _make_parser(job_related_ids: set[str], status_map: dict[str, str] | None = None):
    """Return a fake parse_job_email that classifies messages by their snippet."""
    status_map = status_map or {}

    def _parse(text: str, *, provider: str = "local", gemini_model=None):
        for mid, msg in FAKE_MESSAGES.items():
            snippet = msg.get("snippet", "")
            if snippet and snippet in text:
                is_job = mid in job_related_ids
                return {
                    "is_job_related": is_job,
                    "company": None,
                    "role": None,
                    "status": status_map.get(mid, "Applied") if is_job else None,
                    "interview_date": None,
                    "confidence": 0.9 if is_job else 0.1,
                    "extracted_by": "heuristic",
                }
        return {"is_job_related": False, "company": None, "role": None, "status": None,
                "interview_date": None, "confidence": 0.1, "extracted_by": "heuristic"}

    return _parse


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class WatcherTestBase(unittest.TestCase):
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
        """Insert a bare application and return its id."""
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

    def _set_watchers(self, application_id: str, patterns: list[str]) -> None:
        conn = self._conn()
        try:
            set_watchers_for_application(conn, application_id, patterns)
            conn.commit()
        finally:
            conn.close()

    def _get_watchers(self, application_id: str) -> list[str]:
        conn = self._conn()
        try:
            return fetch_watchers_for_application(conn, application_id)
        finally:
            conn.close()

    def _run_sync(self, message_ids: list[str], parser=None) -> dict:
        messages = {mid: FAKE_MESSAGES[mid] for mid in message_ids}
        if parser is None:
            parser = _make_parser(set(message_ids))
        with (
            patch("app.gmail._credentials_from_row", return_value=FakeCredentials()),
            patch("app.gmail._refresh_credentials_if_needed", return_value=None),
            patch("app.gmail._build_gmail_service", return_value=object()),
            patch("app.gmail._list_message_ids", return_value=message_ids),
            patch("app.gmail._get_message", side_effect=lambda _s, mid: messages[mid]),
            patch("app.gmail.parse_job_email", side_effect=parser),
        ):
            return sync_gmail_messages(self.app)


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------

class TestWatcherCRUD(WatcherTestBase):
    def test_set_and_fetch_patterns(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io", "noreply@lever.co"])
        patterns = self._get_watchers(app_id)
        self.assertIn("@greenhouse.io", patterns)
        self.assertIn("noreply@lever.co", patterns)

    def test_patterns_normalised_to_lowercase(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@Greenhouse.IO"])
        self.assertEqual(self._get_watchers(app_id), ["@greenhouse.io"])

    def test_duplicates_deduplicated(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io", "@greenhouse.io"])
        self.assertEqual(len(self._get_watchers(app_id)), 1)

    def test_set_replaces_existing(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        self._set_watchers(app_id, ["@lever.co"])
        self.assertEqual(self._get_watchers(app_id), ["@lever.co"])

    def test_set_empty_clears_all(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        self._set_watchers(app_id, [])
        self.assertEqual(self._get_watchers(app_id), [])

    def test_delete_watchers_for_application(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        conn = self._conn()
        try:
            delete_watchers_for_application(conn, app_id)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._get_watchers(app_id), [])

    def test_watchers_scoped_to_application(self) -> None:
        id1 = self._create_application("Google", "SWE")
        id2 = self._create_application("Stripe", "Backend")
        self._set_watchers(id1, ["@greenhouse.io"])
        self._set_watchers(id2, ["@lever.co"])
        self.assertEqual(self._get_watchers(id1), ["@greenhouse.io"])
        self.assertEqual(self._get_watchers(id2), ["@lever.co"])


# ---------------------------------------------------------------------------
# Pattern matching tests
# ---------------------------------------------------------------------------

class TestWatcherMatching(WatcherTestBase):
    def test_domain_pattern_matches_bare_email(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "no-reply@greenhouse.io")
        finally:
            conn.close()
        self.assertEqual(result, app_id)

    def test_domain_pattern_matches_display_name_format(self) -> None:
        app_id = self._create_application("Stripe", "Backend")
        self._set_watchers(app_id, ["@stripe.com"])
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "Stripe Recruiting <recruiting@stripe.com>")
        finally:
            conn.close()
        self.assertEqual(result, app_id)

    def test_full_address_pattern_matches(self) -> None:
        app_id = self._create_application("Meta", "PM")
        self._set_watchers(app_id, ["recruiting@meta.com"])
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "recruiting@meta.com")
        finally:
            conn.close()
        self.assertEqual(result, app_id)

    def test_no_match_returns_none(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "hr@unknownco.io")
        finally:
            conn.close()
        self.assertIsNone(result)

    def test_empty_sender_returns_none(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "")
        finally:
            conn.close()
        self.assertIsNone(result)

    def test_matching_is_case_insensitive(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@GREENHOUSE.IO"])
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "Noreply@GREENHOUSE.IO")
        finally:
            conn.close()
        self.assertEqual(result, app_id)

    def test_no_watchers_returns_none(self) -> None:
        conn = self._conn()
        try:
            result = match_application_by_sender(conn, "user@any.com")
        finally:
            conn.close()
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Gmail sync — watcher branch
# ---------------------------------------------------------------------------

class TestGmailSyncWatcherBranch(WatcherTestBase):
    def test_watcher_routes_email_to_correct_application(self) -> None:
        """Email from @greenhouse.io updates the Google application, not a new record."""
        self._seed_gmail_connection()
        app_id = self._create_application("Google", "Software Engineer", "Applied")
        self._set_watchers(app_id, ["@greenhouse.io"])

        parser = _make_parser(
            {"msg-greenhouse"},
            status_map={"msg-greenhouse": "Interview Scheduled"},
        )
        result = self._run_sync(["msg-greenhouse"], parser)

        self.assertTrue(result["ok"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["created"], 0)

        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["company"], "Google")  # company never overwritten
        self.assertEqual(apps[0]["status"], "Interview Scheduled")
        self.assertEqual(apps[0]["source_type"], "gmail")

    def test_watcher_branch_respects_status_pipeline(self) -> None:
        """Status only advances — a watcher email with a lower status is ignored."""
        self._seed_gmail_connection()
        app_id = self._create_application("Google", "SWE", "Interview Scheduled")
        self._set_watchers(app_id, ["@greenhouse.io"])

        parser = _make_parser({"msg-greenhouse"}, status_map={"msg-greenhouse": "Applied"})
        self._run_sync(["msg-greenhouse"], parser)

        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(apps[0]["status"], "Interview Scheduled")  # not downgraded

    def test_watcher_skips_non_job_related_email(self) -> None:
        """Even with a matching watcher, a non-job-related email (e.g. billing) is skipped."""
        self._seed_gmail_connection()
        app_id = self._create_application("LinkedIn", "PM", "Applied")
        self._set_watchers(app_id, ["@linkedin.com"])

        # msg-not-job is a billing email from billing@linkedin.com — not job-related.
        parser = _make_parser(set())  # no messages marked as job-related
        result = self._run_sync(["msg-not-job"], parser)

        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["updated"], 0)
        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(apps[0]["status"], "Applied")  # unchanged

    def test_watcher_deduplicates_second_sync(self) -> None:
        """Syncing the same message twice doesn't double-update."""
        self._seed_gmail_connection()
        app_id = self._create_application("Google", "SWE", "Applied")
        self._set_watchers(app_id, ["@greenhouse.io"])

        parser = _make_parser({"msg-greenhouse"}, status_map={"msg-greenhouse": "Interview Scheduled"})
        self._run_sync(["msg-greenhouse"], parser)
        result2 = self._run_sync(["msg-greenhouse"], parser)

        self.assertEqual(result2["skipped"], 1)
        self.assertEqual(result2["updated"], 0)

    def test_multiple_applications_each_with_own_watcher(self) -> None:
        """Two applications with different watchers both get updated."""
        self._seed_gmail_connection()
        goog_id = self._create_application("Google", "SWE", "Applied")
        stripe_id = self._create_application("Stripe", "Backend", "Applied")
        self._set_watchers(goog_id, ["@greenhouse.io"])
        self._set_watchers(stripe_id, ["@stripe.com"])

        parser = _make_parser(
            {"msg-greenhouse", "msg-stripe"},
            status_map={"msg-greenhouse": "Interview Scheduled", "msg-stripe": "Technical Test"},
        )
        result = self._run_sync(["msg-greenhouse", "msg-stripe"], parser)

        self.assertEqual(result["updated"], 2)
        apps = {a["company"]: a for a in self.app.test_client().get("/api/applications").get_json()}
        self.assertEqual(apps["Google"]["status"], "Interview Scheduled")
        self.assertEqual(apps["Stripe"]["status"], "Technical Test")


# ---------------------------------------------------------------------------
# Gmail sync — fallback branch (no watcher)
# ---------------------------------------------------------------------------

class TestGmailSyncFallbackBranch(WatcherTestBase):
    def test_fallback_creates_new_application_when_no_watcher(self) -> None:
        self._seed_gmail_connection()

        def parser(text, *, provider="local", gemini_model=None):
            return {
                "is_job_related": True,
                "company": "RandomCo",
                "role": "Product Manager",
                "status": "Applied",
                "interview_date": None,
                "confidence": 0.8,
                "extracted_by": "heuristic",
            }

        result = self._run_sync(["msg-unknown"], parser)

        self.assertEqual(result["created"], 1)
        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(apps[0]["company"], "RandomCo")

    def test_fallback_stamps_gmail_message_id_on_fuzzy_matched_record(self) -> None:
        """If a fuzzy-matched manual record has no gmail_message_id, the current id is saved."""
        self._seed_gmail_connection()
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO applications (
                    id, company, role, source, status, applied_date,
                    source_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("manual-1", "RandomCo", "Product Manager", "Other",
                 "Applied", "2026-04-01", "manual", utc_now(), utc_now()),
            )
            conn.commit()
        finally:
            conn.close()

        def parser(text, *, provider="local", gemini_model=None):
            return {"is_job_related": True, "company": "RandomCo", "role": "Product Manager",
                    "status": "Applied", "interview_date": None, "confidence": 0.8,
                    "extracted_by": "heuristic"}

        self._run_sync(["msg-unknown"], parser)
        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["gmail_message_id"], "msg-unknown")


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestWatcherRoutes(WatcherTestBase):
    def test_update_route_saves_watchers(self) -> None:
        app_id = self._create_application("Google", "SWE")
        client = self.app.test_client()
        client.post(
            f"/applications/{app_id}/update",
            data={
                "company": "Google",
                "role": "SWE",
                "status": "Applied",
                "applied_date": "2026-04-01",
                "source": "Other",
                "source_type": "manual",
                "watcher_patterns": ["@greenhouse.io", "noreply@lever.co"],
            },
            follow_redirects=True,
        )
        patterns = self._get_watchers(app_id)
        self.assertIn("@greenhouse.io", patterns)
        self.assertIn("noreply@lever.co", patterns)

    def test_update_route_replaces_watchers(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        client = self.app.test_client()
        client.post(
            f"/applications/{app_id}/update",
            data={
                "company": "Google",
                "role": "SWE",
                "status": "Applied",
                "applied_date": "2026-04-01",
                "source": "Other",
                "source_type": "manual",
                "watcher_patterns": ["@lever.co"],
            },
            follow_redirects=True,
        )
        patterns = self._get_watchers(app_id)
        self.assertNotIn("@greenhouse.io", patterns)
        self.assertIn("@lever.co", patterns)

    def test_delete_route_removes_watchers(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io"])
        client = self.app.test_client()
        client.post(f"/applications/{app_id}/delete", follow_redirects=True)
        # Application should be gone and watchers should be cleaned up.
        apps = client.get("/api/applications").get_json()
        self.assertEqual(apps, [])
        patterns = self._get_watchers(app_id)
        self.assertEqual(patterns, [])

    def test_api_watchers_endpoint_returns_patterns(self) -> None:
        app_id = self._create_application("Google", "SWE")
        self._set_watchers(app_id, ["@greenhouse.io", "jobs@google.com"])
        client = self.app.test_client()
        response = client.get(f"/api/applications/{app_id}/watchers")
        self.assertEqual(response.status_code, 200)
        patterns = response.get_json()
        self.assertIn("@greenhouse.io", patterns)
        self.assertIn("jobs@google.com", patterns)

    def test_api_watchers_endpoint_returns_empty_list_for_no_watchers(self) -> None:
        app_id = self._create_application("Google", "SWE")
        client = self.app.test_client()
        response = client.get(f"/api/applications/{app_id}/watchers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])


# ---------------------------------------------------------------------------
# Regression: expired credentials with no refresh token
# ---------------------------------------------------------------------------

class TestRefreshCredentialsRegression(WatcherTestBase):
    def test_sync_reports_error_when_token_expired_no_refresh_token(self) -> None:
        self._seed_gmail_connection()

        class ExpiredNoRefresh:
            valid = False
            expired = True
            refresh_token = None

            def to_json(self) -> str:
                return "{}"

        with patch("app.gmail._credentials_from_row", return_value=ExpiredNoRefresh()):
            result = sync_gmail_messages(self.app)

        self.assertFalse(result["ok"])
        self.assertIn("reconnect", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
