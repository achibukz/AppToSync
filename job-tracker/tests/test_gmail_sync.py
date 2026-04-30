from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from app import create_app
from app.config import GMAIL_REDIRECT_URI
from app.database import connect_db
from app.gmail import start_gmail_authorization, sync_gmail_messages
from app.utils import utc_now


class GmailSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": self.database_path,
                "SEED_DEMO_DATA": False,
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_gmail_callback_route_redirects_after_authorization(self) -> None:
        client = self.app.test_client()

        with client.session_transaction() as session_data:
            session_data["gmail_oauth_state"] = "oauth-state"
            session_data["gmail_oauth_code_verifier"] = "verifier-123"

        with patch("app.routes.finish_gmail_authorization") as mock_finish:
            mock_finish.return_value = {"ok": True, "email": "me@example.com"}
            response = client.get("/gmail/callback?code=test&state=oauth-state")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        mock_finish.assert_called_once()
        call_args = mock_finish.call_args.args
        self.assertEqual(call_args[1].startswith("http://localhost"), True)
        self.assertEqual(call_args[2], "oauth-state")
        self.assertEqual(call_args[3], "verifier-123")

    def test_start_gmail_authorization_uses_env_redirect_uri_and_returns_code_verifier(self) -> None:
        fake_flow = unittest.mock.Mock()
        fake_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth", "state-123")
        fake_flow.code_verifier = "verifier-abc"

        with patch.dict(
            "os.environ",
            {
                "GMAIL_CLIENT_ID": "client-id",
                "GMAIL_CLIENT_SECRET": "client-secret",
                "GMAIL_REDIRECT_URI": "http://127.0.0.1:3000/gmail/callback",
            },
        ), patch("app.gmail.Flow.from_client_config", return_value=fake_flow) as mock_from_client_config:
            authorization_url, state, code_verifier = start_gmail_authorization()

        self.assertEqual(authorization_url, "https://accounts.google.com/o/oauth2/auth")
        self.assertEqual(state, "state-123")
        self.assertEqual(code_verifier, "verifier-abc")
        self.assertEqual(fake_flow.redirect_uri, "http://127.0.0.1:3000/gmail/callback")
        mock_from_client_config.assert_called_once()

    def test_gmail_status_route_reports_connection_state(self) -> None:
        connection = connect_db(self.app)
        try:
            connection.execute(
                """
                INSERT INTO gmail_connections (
                    id, credentials_json, connected_email, connected_at, last_sync_at,
                    last_sync_error, sync_interval_minutes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    '{"token":"x","refresh_token":"y","token_uri":"https://oauth2.googleapis.com/token","client_id":"id","client_secret":"secret","scopes":["https://www.googleapis.com/auth/gmail.readonly"]}',
                    "user@example.com",
                    utc_now(),
                    utc_now(),
                    None,
                    15,
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        client = self.app.test_client()
        response = client.get("/gmail/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["connected_email"], "user@example.com")

    def test_gmail_sync_stores_messages_and_queues_for_review(self) -> None:
        """Sync should fetch each message, parse via Gemini, and queue unmatched ones for review."""
        connection = connect_db(self.app)
        try:
            connection.execute(
                """
                INSERT INTO gmail_connections (
                    id, credentials_json, connected_email, connected_at, last_sync_at,
                    last_sync_error, sync_interval_minutes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    '{"token":"x","refresh_token":"y","token_uri":"https://oauth2.googleapis.com/token","client_id":"id","client_secret":"secret","scopes":["https://www.googleapis.com/auth/gmail.readonly"]}',
                    "user@example.com",
                    utc_now(),
                    None,
                    None,
                    15,
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        class FakeCredentials:
            valid = True
            expired = False

            def __init__(self) -> None:
                self.refresh_token = "refresh-token"

            def to_json(self) -> str:
                return '{"token":"x","refresh_token":"y"}'

        messages = {
            "gmail-1": {
                "internalDate": "1745900000000",
                "payload": {"headers": [{"name": "Subject", "value": "Thanks for applying"}, {"name": "From", "value": "hr@example.co"}]},
                "snippet": "Hello",
            },
            "gmail-2": {
                "internalDate": "1745900100000",
                "payload": {"headers": [{"name": "Subject", "value": "Interview follow-up"}, {"name": "From", "value": "hr@example.co"}]},
                "snippet": "Hello again",
            },
        }

        def fake_strict_parser(email_text: str, gemini_model: str | None = None):
            return ({
                "is_job_related": True,
                "company": "Example Co",
                "role": "Product Designer",
                "status": "Interview Scheduled" if "Interview" in email_text else "Applied",
                "interview_date": None,
                "confidence": 0.95,
                "extracted_by": "gemini",
                "reasoning_summary": None,
                "field_explanations": {},
            }, None)

        with patch("app.gmail._credentials_from_row", return_value=FakeCredentials()), patch(
            "app.gmail._refresh_credentials_if_needed", return_value=None
        ), patch("app.gmail._build_gmail_service", return_value=object()), patch(
            "app.gmail._list_message_ids", return_value=["gmail-1", "gmail-2"]
        ), patch("app.gmail._get_message", side_effect=lambda service, message_id: messages[message_id]), patch(
            "app.gmail.parse_job_email_strict", side_effect=fake_strict_parser
        ):
            result = sync_gmail_messages(self.app)

        self.assertTrue(result["ok"])
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["parsed"], 2)
        # Both fall through to pending review (no existing app to auto-update).
        self.assertEqual(result["pending_review"], 2)

        # No applications auto-created.
        apps = self.app.test_client().get("/api/applications").get_json()
        self.assertEqual(apps, [])

        # Both emails are in the queue.
        emails = self.app.test_client().get("/api/emails").get_json()
        self.assertEqual(len(emails["pending_review"]), 2)

    def test_gmail_sync_dedup_second_sync_is_noop(self) -> None:
        connection = connect_db(self.app)
        try:
            connection.execute(
                """
                INSERT INTO gmail_connections (
                    id, credentials_json, connected_email, connected_at, last_sync_at,
                    last_sync_error, sync_interval_minutes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    '{"token":"x","refresh_token":"y","token_uri":"https://oauth2.googleapis.com/token","client_id":"id","client_secret":"secret","scopes":["https://www.googleapis.com/auth/gmail.readonly"]}',
                    "user@example.com",
                    utc_now(),
                    None,
                    None,
                    15,
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        class FakeCredentials:
            valid = True
            expired = False
            refresh_token = "y"

            def to_json(self) -> str:
                return '{"token":"x"}'

        messages = {
            "gmail-1": {"internalDate": "1745900000000",
                        "payload": {"headers": [{"name": "From", "value": "hr@example.co"}, {"name": "Subject", "value": "Thanks"}]},
                        "snippet": "thanks"},
        }

        def fake_strict_parser(email_text: str, gemini_model: str | None = None):
            return ({
                "is_job_related": True, "company": "X", "role": "Y", "status": "Applied",
                "interview_date": None, "confidence": 0.9, "extracted_by": "gemini",
                "reasoning_summary": None, "field_explanations": {},
            }, None)

        common_patches = [
            patch("app.gmail._credentials_from_row", return_value=FakeCredentials()),
            patch("app.gmail._refresh_credentials_if_needed", return_value=None),
            patch("app.gmail._build_gmail_service", return_value=object()),
            patch("app.gmail._list_message_ids", return_value=["gmail-1"]),
            patch("app.gmail._get_message", side_effect=lambda s, mid: messages[mid]),
            patch("app.gmail.parse_job_email_strict", side_effect=fake_strict_parser),
        ]
        for p in common_patches:
            p.start()
        try:
            first = sync_gmail_messages(self.app)
            second = sync_gmail_messages(self.app)
        finally:
            for p in common_patches:
                p.stop()

        self.assertEqual(first["fetched"], 1)
        self.assertEqual(second["fetched"], 0)
        self.assertEqual(second["parsed"], 0)


if __name__ == "__main__":
    unittest.main()
