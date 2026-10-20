"""SSNs, ITINs and bank numbers are masked before any email text reaches the AI."""

from __future__ import annotations

from src.config import PROJECT_ROOT
from src.input_reader import read_intake_email
from src.redaction import BANK, SSN, redact_sensitive_numbers


def test_dashed_ssn_is_redacted():
    result = redact_sensitive_numbers("SSN: 123-45-6789\nPhone: (312) 555-0162")

    assert "123-45-6789" not in result.text
    assert "SSN: [REDACTED SSN/ITIN]" in result.text
    assert "(312) 555-0162" in result.text, "phone numbers are not SSNs"
    assert result.removed == {SSN: 1}


def test_labeled_undashed_ssn_and_itin_are_redacted():
    result = redact_sensitive_numbers("my social security number 123456789 and ITIN #912345678")

    assert "123456789" not in result.text
    assert "912345678" not in result.text
    assert result.removed == {SSN: 2}


def test_bank_numbers_are_redacted_but_labels_kept():
    result = redact_sensitive_numbers("routing number 021000021, account number 4455667788.")

    assert result.text == "routing number [REDACTED bank account/routing number], account number " \
        "[REDACTED bank account/routing number]."
    assert result.removed == {BANK: 2}


def test_intake_details_are_left_alone():
    text = (
        "Driver's license: NC 000184529311, expires 07/22/2031\n"
        "Address: 58 Willow Bend Ct, Cary, NC 27519-1234\n"
        "Date of birth: 1986-07-22\nPhone: (919) 555-0183\nI have a savings account."
    )
    result = redact_sensitive_numbers(text)

    assert result.text == text
    assert not result.changed


def test_sample_with_an_ssn_is_redacted():
    text = read_intake_email(PROJECT_ROOT / "sample_inputs" / "06_irs_notice_cp2000_angela_brooks.pdf").text

    result = redact_sensitive_numbers(text)

    assert "123-45-6789" in text
    assert "123-45-6789" not in result.text
    assert "B42071986530" in result.text, "the driver's license number is a required field and stays"
