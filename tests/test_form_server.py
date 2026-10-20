"""The local intake-system stand-in: serves the form, validates submissions, issues intake IDs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date

import pytest

from src.config import PROJECT_ROOT
from src.form_server import IntakeServer, validate_submission
from src.verification import INTAKE_ID_RE

VALID = {
    "full_name": "Test Client",
    "email": "client@example.com",
    "phone": "555-010-0000",
    "date_of_birth": "1990-01-31",
    "street_address": "100 Main St",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
    "drivers_license_number": "12345678",
    "drivers_license_state": "TX",
    "drivers_license_expires_on": "2030-01-31",
    "tax_year": "2025",
    "urgency": "low",
    "income_sources": ["w2_wages"],
    "deductions": [],
    "notes": "",
}


@pytest.fixture(scope="module")
def server():
    with IntakeServer(PROJECT_ROOT / "intake_form") as running:
        yield running


def post(server, payload):
    request = urllib.request.Request(
        server.url + "api/intake",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_serves_the_intake_form(server):
    with urllib.request.urlopen(server.url, timeout=5) as response:
        assert "New tax client intake" in response.read().decode()


def test_valid_submission_gets_an_intake_id_and_is_stored(server):
    status, body = post(server, VALID)

    assert status == 201
    assert INTAKE_ID_RE.match(body["intake_id"])
    stored = server.get_submission(body["intake_id"])
    assert stored["email"] == VALID["email"]
    assert stored["income_sources"] == ["w2_wages"]


def test_incomplete_submission_is_rejected(server):
    status, body = post(server, {**VALID, "drivers_license_number": ""})

    assert status == 422
    assert "drivers_license_number is required" in body["problems"]


def test_validate_submission_checks_allowed_values():
    problems = validate_submission(
        {**VALID, "state": "ZZ", "urgency": "urgent", "date_of_birth": "31/01/1990", "zip_code": "787"}
    )

    assert len(problems) == 4


def test_validate_submission_checks_tax_fields():
    problems = validate_submission(
        {
            **VALID,
            "tax_year": str(date.today().year + 1),
            "number_of_dependents": "-2",
            "filing_status": "complicated",
            "income_sources": ["lottery"],
            "deductions": "mortgage_interest",
        }
    )

    assert len(problems) == 5


def test_blank_optional_fields_are_accepted():
    blanks = {"marital_status": "", "filing_status": "", "deadline_date": "", "number_of_dependents": "", "occupation": ""}

    assert validate_submission({**VALID, **blanks}) == []
