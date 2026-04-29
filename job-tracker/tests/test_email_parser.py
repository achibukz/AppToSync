from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import DEFAULT_GEMINI_MODEL
from app.email_parser import local_parse_job_email, parse_job_email


class EmailParserTests(unittest.TestCase):
    def test_local_parser_detects_interview_email(self) -> None:
        result = local_parse_job_email(
            "Thanks for applying to Product Designer at Canva. We would like to schedule an interview on April 30, 2026 at 2:00 PM."
        )
        self.assertTrue(result["is_job_related"])
        self.assertEqual(result["status"], "Interview Scheduled")
        self.assertEqual(result["company"], "Canva")
        self.assertEqual(result["interview_date"], "2026-04-30")

    def test_gemini_parser_uses_api_response_shape(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
