from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from app import create_app
from app.config import DEFAULT_GEMINI_MODEL
from app.email_parser import local_parse_job_email, parse_job_email


class JobTrackerAppTests(unittest.TestCase):
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

    def test_local_parser_detects_interview_email(self) -> None:
        result = local_parse_job_email(
            "Thanks for applying to Product Designer at Canva. We would like to schedule an interview on April 30, 2026 at 2:00 PM."
        )
        self.assertTrue(result["is_job_related"])
        self.assertEqual(result["status"], "Interview Scheduled")
        self.assertEqual(result["company"], "Canva")
        self.assertEqual(result["interview_date"], "2026-04-30")

    def test_gemini_parser_uses_api_response_shape(self) -> None:
        """Test that Gemini parser properly formats API responses."""
        class FakeContent:
            text = '{"is_job_related":true,"company":"Google","role":"Software Engineer","status":"Interview Scheduled","interview_date":"2026-05-01","confidence":0.93,"extracted_by":"gemini","reasoning_summary":"","field_explanations":{}}'

        class FakeResponse:
            def __init__(self):
                self.text = FakeContent.text

        def mock_generate_content(*args, **kwargs):
            return FakeResponse()

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.email_parser.genai.Client"
        ) as mock_client:
            mock_instance = mock_client.return_value
            mock_instance.models.generate_content = mock_generate_content
            result = parse_job_email("Interview request from Google", provider="gemini")

        self.assertEqual(result["provider"], "gemini")
        self.assertTrue(result["is_job_related"])
        self.assertEqual(result["company"], "Google")
        self.assertEqual(result["role"], "Software Engineer")
        self.assertEqual(result["status"], "Interview Scheduled")
        self.assertEqual(result["interview_date"], "2026-05-01")

    def test_gemini_parser_uses_selected_model(self) -> None:
        """Test that parse_job_email forwards the selected Gemini model."""
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.email_parser.genai.Client"
        ) as mock_client:
            mock_response = type("Response", (), {"text": '{"is_job_related":true}'})()
            mock_client.return_value.models.generate_content.return_value = mock_response

            parse_job_email(
                "Interview request from Google",
                provider="gemini",
                gemini_model="gemini-2.5-flash-lite",
            )

            kwargs = mock_client.return_value.models.generate_content.call_args.kwargs
            self.assertEqual(kwargs["model"], "gemini-2.5-flash-lite")

    def test_gemini_parser_falls_back_to_default_model(self) -> None:
        """Test that unsupported Gemini models fall back to the default option."""
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.email_parser.genai.Client"
        ) as mock_client:
            mock_response = type("Response", (), {"text": '{"is_job_related":true}'})()
            mock_client.return_value.models.generate_content.return_value = mock_response

            parse_job_email(
                "Interview request from Google",
                provider="gemini",
                gemini_model="unsupported-model",
            )

            kwargs = mock_client.return_value.models.generate_content.call_args.kwargs
            self.assertEqual(kwargs["model"], DEFAULT_GEMINI_MODEL)

    def test_api_create_and_list_application(self) -> None:
        client = self.app.test_client()
        response = client.post(
            "/api/applications",
            json={
                "company": "OpenAI",
                "role": "Developer Advocate",
                "status": "Applied",
                "applied_date": "2026-04-28",
                "source": "Direct",
                "source_type": "manual",
            },
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["company"], "OpenAI")

        list_response = client.get("/api/applications")
        self.assertEqual(list_response.status_code, 200)
        applications = list_response.get_json()
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["role"], "Developer Advocate")

    def test_api_delete_application(self) -> None:
        client = self.app.test_client()
        created = client.post(
            "/api/applications",
            json={
                "company": "Figma",
                "role": "Designer",
                "status": "Applied",
                "applied_date": "2026-04-28",
            },
        ).get_json()

        delete_response = client.delete(f"/api/applications/{created['id']}")
        self.assertEqual(delete_response.status_code, 200)

        empty_response = client.get("/api/applications")
        self.assertEqual(empty_response.get_json(), [])


if __name__ == "__main__":
    unittest.main()