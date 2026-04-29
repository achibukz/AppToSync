"""
Unit and integration tests for the Gemini email parser.

This test suite covers:
- Real API calls with actual emails (integration tests)
- Error handling and edge cases
- JSON response parsing
- Output structure validation
"""

import json
import os
import sys
from pathlib import Path
from unittest import mock
from typing import Any

import pytest
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.email_parser import gemini_parse_job_email, gemini_parse_job_email_with_error


# Load environment variables
load_dotenv()


class TestGeminiEmailParserIntegration:
    """Integration tests with real Gemini API calls."""

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_interview_invitation_email(self):
        """Test parsing an interview invitation email."""
        email = (
            "Thanks for applying to Product Designer at Canva. "
            "We would like to schedule an interview on April 30, 2026 at 2:00 PM."
        )

        result, error = gemini_parse_job_email_with_error(email)

        assert error is None, f"Unexpected error: {error}"
        assert result is not None
        assert result["is_job_related"] is True
        assert result["company"] is not None  # Should detect Canva
        assert result["role"] is not None  # Should detect Product Designer
        assert result["status"] == "Interview Scheduled"
        assert result["interview_date"] is not None
        assert result["confidence"] > 0.5
        assert "extracted_by" in result
        assert "reasoning_summary" in result
        assert "field_explanations" in result

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_offer_email(self):
        """Test parsing a job offer email."""
        email = (
            "Congratulations! We are pleased to offer you the position of "
            "Senior Software Engineer at Google. Your start date will be June 1, 2026."
        )

        result, error = gemini_parse_job_email_with_error(email)

        assert error is None, f"Unexpected error: {error}"
        assert result is not None
        assert result["is_job_related"] is True
        assert result["company"] is not None  # Should detect Google
        assert result["role"] is not None  # Should detect Senior Software Engineer
        assert result["status"] == "Offer Received"
        assert result["confidence"] > 0.6

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_rejection_email(self):
        """Test parsing a rejection email."""
        email = (
            "Thank you for applying to the UX Designer position at Meta. "
            "While your qualifications were impressive, we have decided to move forward "
            "with other candidates at this time."
        )

        result, error = gemini_parse_job_email_with_error(email)

        assert error is None, f"Unexpected error: {error}"
        assert result is not None
        assert result["is_job_related"] is True
        assert result["company"] is not None  # Should detect Meta
        assert result["role"] is not None  # Should detect UX Designer
        assert result["status"] == "Rejected"
        assert result["confidence"] > 0.5

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_technical_test_email(self):
        """Test parsing a technical assessment email."""
        email = (
            "Hi John, thanks for your interest in the Backend Engineer role at Stripe. "
            "Your next step is to complete our coding assessment on HackerRank. "
            "You have 48 hours to complete it."
        )

        result, error = gemini_parse_job_email_with_error(email)

        assert error is None, f"Unexpected error: {error}"
        assert result is not None
        assert result["is_job_related"] is True
        assert result["company"] is not None  # Should detect Stripe
        assert result["role"] is not None  # Should detect Backend Engineer
        assert result["status"] == "Technical Test"

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_non_job_email(self):
        """Test that non-job emails are correctly identified."""
        email = (
            "Hi there, just checking in to see how you've been. "
            "Would love to catch up over coffee next week!"
        )

        result, error = gemini_parse_job_email_with_error(email)

        assert error is None, f"Unexpected error: {error}"
        assert result is not None
        assert result["is_job_related"] is False
        assert result["company"] is None
        assert result["role"] is None

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_response_structure(self):
        """Test that all required fields are present in the response."""
        email = "Thanks for applying to Data Scientist at Microsoft."

        result, error = gemini_parse_job_email_with_error(email)

        assert error is None
        assert result is not None

        required_fields = [
            "is_job_related",
            "company",
            "role",
            "status",
            "interview_date",
            "confidence",
            "extracted_by",
            "reasoning_summary",
            "field_explanations",
        ]

        for field in required_fields:
            assert field in result, f"Missing required field: {field}"


class TestGeminiEmailParserErrors:
    """Test error handling and edge cases."""

    def test_missing_api_key(self):
        """Test that proper error is returned when API key is missing."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result, error = gemini_parse_job_email_with_error("Test email")

            assert result is None
            assert error is not None
            assert "GEMINI_API_KEY" in error or "GOOGLE_API_KEY" in error

    @mock.patch("app.email_parser.genai.Client")
    def test_api_exception_handling(self, mock_client):
        """Test that API exceptions are caught and returned as errors."""
        mock_client.return_value.models.generate_content.side_effect = Exception(
            "API Connection Error"
        )

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result, error = gemini_parse_job_email_with_error("Test email")

            assert result is None
            assert error is not None
            assert "API Connection Error" in error

    @mock.patch("app.email_parser.genai.Client")
    def test_empty_response(self, mock_client):
        """Test handling of empty API response."""
        mock_response = mock.Mock()
        mock_response.text = ""
        mock_client.return_value.models.generate_content.return_value = mock_response

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result, error = gemini_parse_job_email_with_error("Test email")

            assert result is None
            assert error == "Gemini response did not include parsed text."

    @mock.patch("app.email_parser.genai.Client")
    def test_invalid_json_response(self, mock_client):
        """Test handling of invalid JSON in response."""
        mock_response = mock.Mock()
        mock_response.text = "Not valid JSON at all"
        mock_client.return_value.models.generate_content.return_value = mock_response

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result, error = gemini_parse_job_email_with_error("Test email")

            assert result is None
            assert error == "Gemini response text was not valid JSON."

    @mock.patch("app.email_parser.genai.Client")
    def test_valid_json_response_structure(self, mock_client):
        """Test parsing a valid JSON response from Gemini."""
        mock_response = mock.Mock()
        mock_response.text = json.dumps(
            {
                "is_job_related": True,
                "company": "TechCorp",
                "role": "Senior Engineer",
                "status": "Interview Scheduled",
                "interview_date": "2026-05-15",
                "confidence": 0.92,
                "extracted_by": "gemini",
                "reasoning_summary": "Email mentions interview scheduling.",
                "field_explanations": {
                    "company": "Extracted from 'TechCorp' mention",
                    "status": "Keywords indicate scheduled interview",
                },
            }
        )
        mock_client.return_value.models.generate_content.return_value = mock_response

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result, error = gemini_parse_job_email_with_error("Test email")

            assert error is None
            assert result is not None
            assert result["is_job_related"] is True
            assert result["company"] == "TechCorp"
            assert result["role"] == "Senior Engineer"
            assert result["status"] == "Interview Scheduled"
            assert result["interview_date"] == "2026-05-15"
            assert result["confidence"] == 0.92

    @mock.patch("app.email_parser.genai.Client")
    def test_missing_optional_fields_in_json(self, mock_client):
        """Test that missing optional fields don't cause errors."""
        mock_response = mock.Mock()
        mock_response.text = json.dumps(
            {
                "is_job_related": True,
                "company": "TechCorp",
                # Missing role, status, interview_date, reasoning_summary
            }
        )
        mock_client.return_value.models.generate_content.return_value = mock_response

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result, error = gemini_parse_job_email_with_error("Test email")

            assert error is None
            assert result is not None
            assert result["company"] == "TechCorp"
            assert result["role"] is None
            assert result["status"] is None
            assert result["interview_date"] is None


class TestGeminiParseFunctionWrapper:
    """Test the wrapper function without error handling."""

    @mock.patch("app.email_parser.genai.Client")
    def test_gemini_parse_job_email_success(self, mock_client):
        """Test gemini_parse_job_email returns result on success."""
        mock_response = mock.Mock()
        mock_response.text = json.dumps(
            {
                "is_job_related": True,
                "company": "TechCorp",
                "role": "Engineer",
                "status": "Applied",
                "interview_date": None,
                "confidence": 0.8,
                "extracted_by": "gemini",
                "reasoning_summary": "Job application email",
                "field_explanations": {},
            }
        )
        mock_client.return_value.models.generate_content.return_value = mock_response

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = gemini_parse_job_email("Test email")

            assert result is not None
            assert result["company"] == "TechCorp"

    @mock.patch("app.email_parser.genai.Client")
    def test_gemini_parse_job_email_failure(self, mock_client):
        """Test gemini_parse_job_email returns None on failure."""
        mock_client.return_value.models.generate_content.side_effect = Exception(
            "API Error"
        )

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = gemini_parse_job_email("Test email")

            assert result is None


class TestIntegrationWithApp:
    """Test integration with the Flask app's parse_job_email function."""

    @pytest.mark.skipif(
        not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        reason="GEMINI_API_KEY or GOOGLE_API_KEY not set",
    )
    def test_parse_email_with_gemini_provider(self):
        """Test the full parse_job_email flow with Gemini provider."""
        from app.email_parser import parse_job_email

        email = "Thanks for applying to Product Manager at Amazon. We would like to schedule an interview."
        result = parse_job_email(email, provider="gemini")

        assert result["is_job_related"] is True
        assert result["provider"] == "gemini"
        # Either Gemini succeeded or fallback to local
        assert result["provider_used"] in ["gemini", "local"]

    def test_parse_email_with_local_provider(self):
        """Test the full parse_job_email flow with local provider."""
        from app.email_parser import parse_job_email

        email = "Thanks for applying to Product Manager at Amazon. We would like to schedule an interview."
        result = parse_job_email(email, provider="local")

        assert result["provider"] == "local"
        assert result["provider_used"] == "local"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
