"""Extraction validation and the retry-once-then-flag policy."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from src.ai_client import AIOutputError
from src.extraction import extract_intake, parse_extraction


def make_payload(**overrides):
    payload = {
        "full_name": "Test Client",
        "email": "test.client@example.com",
        "phone": "(555) 010-0000",
        "date_of_birth": "1990-01-31",
        "street_address": "100 Main St, Apt 2",
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
    payload.update(overrides)
    return payload


class ScriptedAI:
    """Returns scripted extraction outputs in order and records the feedback it was given."""

    name = "scripted test AI"

    def __init__(self, *outputs):
        self._outputs = list(outputs)
        self.previous_errors: list[str | None] = []

    def extract(self, email_text, previous_error=None):
        self.previous_errors.append(previous_error)
        output = self._outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output if isinstance(output, str) else json.dumps(output)


# --- validation -------------------------------------------------------------------------


def test_valid_payload_parses_into_a_complete_record():
    record = parse_extraction(json.dumps(make_payload()))

    assert record.is_complete
    assert record.tax_year == 2025
    assert record.date_of_birth == date(1990, 1, 31)
    assert record.drivers_license_expires_on == date(2030, 1, 31)
    assert record.urgency.level == "medium"


def test_empty_field_is_marked_missing_even_if_the_model_says_complete():
    record = parse_extraction(json.dumps(make_payload(drivers_license_number=None, missing_fields=[])))

    assert record.missing_fields == ["drivers_license_number"]
    assert not record.is_complete
    assert record.missing_field_labels == ["driver's license (or state ID) number"]


def test_blank_and_placeholder_strings_count_as_missing():
    record = parse_extraction(json.dumps(make_payload(phone="   ", zip_code="N/A")))

    assert record.phone is None
    assert record.zip_code is None
    assert record.missing_fields == ["phone", "zip_code"]


def test_fields_the_model_flagged_stay_flagged():
    record = parse_extraction(json.dumps(make_payload(missing_fields=["phone"])))

    assert record.missing_fields == ["phone"]
    assert not record.is_complete


def test_missing_fields_are_deduplicated_in_a_stable_order():
    payload = make_payload(tax_year=None, full_name=None, missing_fields=["tax_year", "tax_year", "full_name"])
    record = parse_extraction(json.dumps(payload))

    assert record.missing_fields == ["full_name", "tax_year"]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"case_type": "H-1B"}, id="case types no longer exist"),
        pytest.param({"urgency": {"level": "critical", "reason": "x"}}, id="unknown urgency level"),
        pytest.param({"urgency": {"level": "high", "reason": "  "}}, id="blank urgency reason"),
        pytest.param({"urgency": "high"}, id="urgency without reason"),
        pytest.param({"email": "not-an-email"}, id="malformed email"),
        pytest.param({"phone": "12"}, id="too few phone digits"),
        pytest.param({"date_of_birth": "14/03/1994"}, id="non-ISO date"),
        pytest.param({"date_of_birth": (date.today() + timedelta(days=1)).isoformat()}, id="future date of birth"),
        pytest.param({"state": "Ontario"}, id="state outside the U.S."),
        pytest.param({"drivers_license_state": "ZZ"}, id="unknown issuing state"),
        pytest.param({"zip_code": "7870"}, id="short ZIP code"),
        pytest.param({"drivers_license_number": "AB"}, id="license number too short"),
        pytest.param({"drivers_license_number": "NOT-PROVIDED-YET"}, id="license number without digits"),
        pytest.param({"drivers_license_expires_on": "1800-01-01"}, id="implausible license expiry"),
        pytest.param({"tax_year": date.today().year + 1}, id="future tax year"),
        pytest.param({"tax_year": 1975}, id="ancient tax year"),
        pytest.param({"number_of_dependents": -1}, id="negative dependents"),
        pytest.param({"income_sources": ["lottery"]}, id="unknown income source"),
        pytest.param({"deductions": ["yacht"]}, id="unknown deduction"),
        pytest.param({"filing_status": "it's complicated"}, id="unknown filing status"),
        pytest.param({"missing_fields": ["ssn"]}, id="unknown missing field"),
        pytest.param({"notes": "extra"}, id="unexpected extra field"),
        pytest.param({"marital_status": "engaged"}, id="unknown marital status"),
        pytest.param({"preferred_contact_method": "fax"}, id="unknown contact method"),
    ],
)
def test_invalid_values_are_rejected(overrides):
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(make_payload(**overrides)))


def test_states_and_choices_are_normalized():
    payload = make_payload(
        state="texas",
        drivers_license_state="New York",
        filing_status="Married Filing Jointly",
        marital_status="Married",
        preferred_contact_method="EMAIL",
        drivers_license_number="s530-4210-7788",
        tax_year="2024",
    )
    record = parse_extraction(json.dumps(payload))

    assert record.state == "TX"
    assert record.drivers_license_state == "NY"
    assert record.filing_status == "married_filing_jointly"
    assert record.marital_status == "married"
    assert record.preferred_contact_method == "email"
    assert record.drivers_license_number == "S530-4210-7788"
    assert record.tax_year == 2024


def test_filing_status_abbreviations_are_understood():
    assert parse_extraction(json.dumps(make_payload(filing_status="HOH"))).filing_status == "head_of_household"


def test_checkbox_lists_are_deduplicated_into_form_order():
    payload = make_payload(income_sources=["rental", "w2_wages", "rental"], deductions=None)
    record = parse_extraction(json.dumps(payload))

    assert record.income_sources == ["w2_wages", "rental"]
    assert record.deductions == []


def test_optional_details_are_normalized_and_never_required():
    payload = make_payload(
        employer="N/A",
        consultation_requested=True,
        irs_notice_received=True,
        deadline_date="2026-11-16",
        number_of_dependents=2,
    )
    record = parse_extraction(json.dumps(payload))

    assert record.is_complete
    assert record.employer is None
    assert record.deadline_date == date(2026, 11, 16)
    assert record.display_value("consultation_requested") == "Yes"
    assert record.display_value("number_of_dependents") == "2"


def test_optional_details_default_to_empty_when_omitted():
    record = parse_extraction(json.dumps(make_payload()))

    assert record.occupation is None
    assert record.income_sources == []
    assert record.is_complete


def test_license_number_is_masked_for_display_and_expiry_is_flagged():
    record = parse_extraction(json.dumps(make_payload(drivers_license_expires_on="2020-05-01")))

    assert record.display_value("drivers_license_number") == "••••5678"
    assert record.license_expired
    assert record.display_value("drivers_license_expires_on") == "2020-05-01 (expired)"


def test_tax_profile_carries_no_identifying_details():
    record = parse_extraction(json.dumps(make_payload(income_sources=["w2_wages"], number_of_dependents=1)))
    described = record.tax_profile().describe()

    assert "Tax year: 2025" in described
    assert "W-2 wages" in described
    for private in ("Test Client", "test.client@example.com", "100 Main St", "12345678", "1990-01-31"):
        assert private not in described


def test_missing_key_is_rejected():
    payload = make_payload()
    del payload["tax_year"]

    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(payload))


def test_non_json_is_rejected():
    with pytest.raises(ValidationError):
        parse_extraction("Sure! Here is the data you asked for.")


def test_json_in_a_code_fence_is_accepted():
    record = parse_extraction("```json\n" + json.dumps(make_payload()) + "\n```")

    assert record.full_name == "Test Client"


# --- retry policy -----------------------------------------------------------------------


def test_valid_first_answer_needs_one_attempt():
    ai = ScriptedAI(make_payload())

    result = extract_intake("email", ai)

    assert result.record is not None
    assert result.attempts == 1
    assert ai.previous_errors == [None]


def test_invalid_answer_is_retried_once_with_the_validation_error():
    ai = ScriptedAI(make_payload(state="Ontario"), make_payload())

    result = extract_intake("email", ai)

    assert result.record is not None
    assert result.attempts == 2
    assert ai.previous_errors[0] is None
    assert "state" in ai.previous_errors[1]


def test_two_invalid_answers_flag_the_record_for_human_review():
    ai = ScriptedAI("not json", make_payload(email="nope"), make_payload())

    result = extract_intake("email", ai)

    assert result.record is None
    assert result.needs_human_review
    assert result.attempts == 2
    assert len(ai.previous_errors) == 2, "must not call the model a third time"
    assert len(result.errors) == 2
    assert result.raw_outputs == ["not json", json.dumps(make_payload(email="nope"))]


def test_refusal_counts_as_an_invalid_attempt():
    ai = ScriptedAI(AIOutputError("the model declined the request"), make_payload())

    result = extract_intake("email", ai)

    assert result.record is not None
    assert result.attempts == 2
    assert ai.previous_errors == [None, None], "a refusal is not validation feedback"


def test_repeated_refusal_is_flagged():
    ai = ScriptedAI(AIOutputError("declined"), AIOutputError("declined"))

    result = extract_intake("email", ai)

    assert result.needs_human_review
    assert result.raw_outputs == []
