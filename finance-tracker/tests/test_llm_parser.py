"""Tests for the Gemini LLM-based PDF parser."""

import json
from datetime import date
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.llm_parser import (
    _dict_to_transaction,
    _parse_gemini_response,
    parse_pdf_with_gemini,
)
from src.models import Transaction


class TestParseGeminiResponse:
    """Tests for _parse_gemini_response."""

    def test_valid_json_array(self):
        raw = json.dumps([{"date": "2025-01-15", "description": "Walmart", "amount": -52.30}])
        result = _parse_gemini_response(raw)
        assert len(result) == 1
        assert result[0]["description"] == "Walmart"

    def test_strips_markdown_fences(self):
        raw = '```json\n[{"date": "2025-01-15", "description": "Test", "amount": -10}]\n```'
        result = _parse_gemini_response(raw)
        assert len(result) == 1

    def test_strips_plain_fences(self):
        raw = '```\n[{"date": "2025-01-15", "description": "Test", "amount": -10}]\n```'
        result = _parse_gemini_response(raw)
        assert len(result) == 1

    def test_invalid_json_returns_empty(self):
        result = _parse_gemini_response("not json at all")
        assert result == []

    def test_non_array_returns_empty(self):
        result = _parse_gemini_response('{"key": "value"}')
        assert result == []

    def test_empty_array(self):
        result = _parse_gemini_response("[]")
        assert result == []


class TestDictToTransaction:
    """Tests for _dict_to_transaction."""

    def test_valid_transaction(self):
        d = {
            "date": "2025-03-15",
            "description": "Starbucks Coffee",
            "amount": -5.75,
            "category": "Dining",
            "type": "debit",
        }
        txn = _dict_to_transaction(d)
        assert txn is not None
        assert txn.date == date(2025, 3, 15)
        assert txn.description == "Starbucks Coffee"
        assert txn.amount == -5.75
        assert txn.category == "Dining"
        assert txn.transaction_type == "debit"

    def test_missing_date_returns_none(self):
        assert _dict_to_transaction({"description": "Test", "amount": -10}) is None

    def test_invalid_date_returns_none(self):
        assert _dict_to_transaction({"date": "not-a-date", "description": "Test", "amount": -10}) is None

    def test_missing_description_returns_none(self):
        assert _dict_to_transaction({"date": "2025-01-01", "description": "", "amount": -10}) is None

    def test_infers_type_from_amount(self):
        txn = _dict_to_transaction({"date": "2025-01-01", "description": "Deposit", "amount": 500, "category": "Income"})
        assert txn.transaction_type == "credit"

        txn2 = _dict_to_transaction({"date": "2025-01-01", "description": "Purchase", "amount": -50, "category": "Shopping"})
        assert txn2.transaction_type == "debit"

    def test_defaults_category_to_other(self):
        txn = _dict_to_transaction({"date": "2025-01-01", "description": "Mystery", "amount": -10})
        assert txn.category == "Other"

    def test_invalid_amount_returns_none(self):
        assert _dict_to_transaction({"date": "2025-01-01", "description": "Test", "amount": "abc"}) is None


class TestParsePdfWithGemini:
    """Integration-style tests for parse_pdf_with_gemini with mocked Gemini API."""

    @patch("src.llm_parser.genai")
    @patch("src.llm_parser._extract_pdf_text")
    def test_successful_parse(self, mock_extract, mock_genai):
        mock_response = MagicMock()
        mock_response.text = json.dumps([
            {"date": "2025-01-15", "description": "Walmart", "amount": -52.30, "category": "Groceries", "type": "debit"},
            {"date": "2025-01-16", "description": "Payroll", "amount": 3000.00, "category": "Income", "type": "credit"},
        ])
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_genai.Client.return_value = mock_client

        # Mock open() for PDF bytes
        with patch("builtins.open", mock_open(read_data=b"fake pdf bytes")):
            result = parse_pdf_with_gemini("/fake/path.pdf", api_key="test-key")

        assert len(result) == 2
        assert result[0].description == "Walmart"
        assert result[0].amount == -52.30
        assert result[0].category == "Groceries"
        assert result[1].amount == 3000.00
        assert result[1].category == "Income"

    @patch("src.llm_parser.genai")
    def test_fallback_to_next_model(self, mock_genai):
        """When primary model fails, tries fallback models."""
        mock_client = MagicMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call (primary model PDF) fails
            if call_count == 1:
                raise Exception("Rate limited")
            # Second call (fallback model PDF) succeeds
            mock_resp = MagicMock()
            mock_resp.text = json.dumps([
                {"date": "2025-01-15", "description": "Walmart", "amount": -52.30, "category": "Groceries", "type": "debit"},
            ])
            return mock_resp

        mock_client.models.generate_content.side_effect = side_effect
        mock_genai.Client.return_value = mock_client

        with patch("builtins.open", mock_open(read_data=b"fake pdf")):
            result = parse_pdf_with_gemini(
                "/fake/path.pdf", api_key="test-key",
                fallback_models=["gemini-2.0-flash-lite"],
            )

        assert len(result) == 1
        assert result[0].description == "Walmart"

    @patch("src.llm_parser.genai")
    def test_fallback_to_text_extraction(self, mock_genai):
        """When all PDF uploads fail, falls back to text extraction."""
        mock_client = MagicMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First 2 calls (PDF uploads for 2 models) fail
            if call_count <= 2:
                raise Exception("PDF upload failed")
            # Text extraction calls succeed
            mock_resp = MagicMock()
            mock_resp.text = json.dumps([
                {"date": "2025-01-15", "description": "Walmart", "amount": -52.30, "category": "Groceries", "type": "debit"},
            ])
            return mock_resp

        mock_client.models.generate_content.side_effect = side_effect
        mock_genai.Client.return_value = mock_client

        with patch("builtins.open", mock_open(read_data=b"fake pdf")):
            with patch("src.llm_parser._extract_pdf_text", return_value="Some extracted text"):
                result = parse_pdf_with_gemini(
                    "/fake/path.pdf", api_key="test-key",
                    fallback_models=["gemini-2.0-flash-lite"],
                )

        assert len(result) == 1
        assert result[0].description == "Walmart"

    @patch("src.llm_parser.genai")
    @patch("src.llm_parser._extract_pdf_text")
    def test_empty_pdf_returns_empty(self, mock_extract, mock_genai):
        mock_extract.return_value = ""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("fail")
        mock_genai.Client.return_value = mock_client

        with patch("builtins.open", mock_open(read_data=b"")):
            result = parse_pdf_with_gemini("/fake/path.pdf", api_key="test-key")
        assert result == []

    @patch("src.llm_parser.genai")
    def test_gemini_error_returns_empty(self, mock_genai):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")
        mock_genai.Client.return_value = mock_client

        with patch("builtins.open", mock_open(read_data=b"fake pdf")):
            with patch("src.llm_parser._extract_pdf_text", return_value=""):
                result = parse_pdf_with_gemini("/fake/path.pdf", api_key="test-key")
        assert result == []

    @patch("src.llm_parser.genai")
    def test_bad_json_returns_empty(self, mock_genai):
        mock_response = MagicMock()
        mock_response.text = "This is not JSON"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_genai.Client.return_value = mock_client

        with patch("builtins.open", mock_open(read_data=b"fake pdf")):
            result = parse_pdf_with_gemini("/fake/path.pdf", api_key="test-key")
        assert result == []
