"""Unit tests for GeminiAI client integration and error handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.ai_client import AIOutputError, GeminiAI
from src.models import IntakeExtraction, TaxProfile


def _make_dummy_response(text: str, in_tokens: int = 100, out_tokens: int = 50, finish_reason: str = "STOP"):
    candidate = MagicMock()
    candidate.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_token_count = in_tokens
    usage.candidates_token_count = out_tokens

    response = MagicMock()
    response.text = text
    response.candidates = [candidate]
    response.usage_metadata = usage
    return response


def test_gemini_ai_extract_success():
    mock_client = MagicMock()
    sample_payload = {
        "full_name": "Test Client",
        "email": "test@example.com",
        "phone": "(555) 010-0000",
        "date_of_birth": "1990-01-31",
        "street_address": "100 Main St",
        "city": "Austin",
        "state": "TX",
        "zip_code": "78701",
        "drivers_license_number": "12345678",
        "drivers_license_state": "TX",
        "drivers_license_expires_on": "2030-01-31",
        "tax_year": 2025,
        "urgency": {"level": "medium", "reason": "Wants the return filed soon."},
        "missing_fields": [],
    }
    mock_client.models.generate_content.return_value = _make_dummy_response(json.dumps(sample_payload))

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    result_raw = ai.extract("Please prepare my 2025 return")

    parsed = IntakeExtraction.model_validate_json(result_raw)
    assert parsed.full_name == "Test Client"
    assert parsed.tax_year == 2025
    assert ai.usage_summary() == "1 Gemini API call(s), 100 input / 50 output tokens"


def test_gemini_ai_declined_error():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        text="", finish_reason="SAFETY"
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    with pytest.raises(AIOutputError, match="Gemini declined"):
        ai.extract("Some sensitive text")


def test_gemini_ai_empty_answer_error():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        text="", finish_reason="STOP"
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    with pytest.raises(AIOutputError, match="empty answer"):
        ai.extract("Some text")


def test_gemini_ai_draft_followup():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        "Subject: Missing Information\n\nPlease send your phone number."
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    record = IntakeExtraction(
        full_name="Marcus Bell",
        email="marcus@example.com",
        phone=None,
        date_of_birth=None,
        street_address=None,
        city="Denver",
        state="CO",
        zip_code=None,
        drivers_license_number=None,
        drivers_license_state=None,
        drivers_license_expires_on=None,
        tax_year=2025,
        urgency={"level": "high", "reason": "Return is late."},
        missing_fields=["phone"],
    )

    draft = ai.draft_followup("email text", record)
    assert "Missing Information" in draft
    assert ai.usage_summary() == "1 Gemini API call(s), 100 input / 50 output tokens"


def test_gemini_ai_draft_checklist():
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_dummy_response(
        "## Income documents\n\n- [ ] Form W-2"
    )
    profile = TaxProfile(
        tax_year=2025, state="TX", marital_status="single", filing_status=None, number_of_dependents=None,
        income_sources=("w2_wages",), deductions=(), irs_notice_received=None,
    )

    ai = GeminiAI(model="gemini-3.5-flash", client=mock_client)
    checklist = ai.draft_checklist(profile)
    assert "## Income documents" in checklist
    sent_prompt = mock_client.models.generate_content.call_args.kwargs["contents"]
    assert "Tax year: 2025" in sent_prompt and "W-2 wages" in sent_prompt


def test_gemini_extraction_schema_asks_for_every_field():
    ai = GeminiAI(model="gemini-3.5-flash", client=MagicMock())

    assert set(ai._schema["required"]) == set(IntakeExtraction.model_fields)
    assert "$ref" not in json.dumps(ai._schema)
