"""Fill the real intake form in headless Chromium and verify the submission.

Skipped when Playwright's Chromium is not installed (`playwright install chromium`).
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Error as PlaywrightError

from src.config import PROJECT_ROOT
from src.extraction import parse_extraction
from src.form_filler import BrowserSession, fill_intake_form
from src.form_server import IntakeServer
from src.verification import verify_submission

RECORD = {
    "full_name": "Test Client",
    "email": "test.client@example.com",
    "phone": "(555) 010-0000",
    "date_of_birth": "1990-01-31",
    "street_address": "100 Main St, Apt 2",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
    "drivers_license_number": "12345678",
    "drivers_license_state": "NY",
    "drivers_license_issued_on": "2022-01-31",
    "drivers_license_expires_on": "2030-01-31",
    "tax_year": 2025,
    "filing_status": "head_of_household",
    "number_of_dependents": 1,
    "income_sources": ["w2_wages", "unemployment"],
    "deductions": ["childcare", "marketplace_insurance"],
    "irs_notice_received": False,
    "deadline_date": "2026-11-16",
    "consultation_requested": True,
    "urgency": {"level": "medium", "reason": "Wants the return filed soon."},
    "missing_fields": [],
}


@pytest.fixture(scope="module")
def page():
    with BrowserSession(headless=True, slow_mo_ms=0) as browser:
        try:
            yield browser.page()
        except PlaywrightError as exc:  # pragma: no cover - depends on the machine
            pytest.skip(f"Chromium is not available: {exc}")


def test_form_is_filled_submitted_and_verified(page, tmp_path):
    record = parse_extraction(json.dumps(RECORD))
    with IntakeServer(PROJECT_ROOT / "intake_form") as server:
        fill_intake_form(page, server.url, record, notes="Test run.")
        result = verify_submission(page, record, server.get_submission, tmp_path, label="e2e")
        stored = server.get_submission(result.intake_id) if result.intake_id else None

    assert result.passed, result.detail
    assert stored["state"] == "TX" and stored["drivers_license_state"] == "NY"
    assert stored["income_sources"] == ["w2_wages", "unemployment"]
    assert stored["irs_notice_received"] == "no"
    assert result.screenshot is not None and result.screenshot.is_file()
    assert "12345678" not in page.locator("#confirmation").inner_text(), "the confirmation masks the license"


def test_incomplete_record_is_never_submitted(page):
    record = parse_extraction(json.dumps({**RECORD, "drivers_license_number": None}))

    with pytest.raises(ValueError, match="incomplete"):
        fill_intake_form(page, "http://127.0.0.1:9/", record, notes="")
