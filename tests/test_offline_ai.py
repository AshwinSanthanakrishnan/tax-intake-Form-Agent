"""The --offline stand-in should drive the sample emails down the intended demo paths."""

from __future__ import annotations

from datetime import date

from src.config import PROJECT_ROOT
from src.extraction import extract_intake
from src.input_reader import read_intake_email
from src.offline_ai import OfflineAI
from src.redaction import redact_sensitive_numbers

SAMPLES = PROJECT_ROOT / "sample_inputs"


def extract(filename):
    text = redact_sensitive_numbers(read_intake_email(SAMPLES / filename).text).text
    result = extract_intake(text, OfflineAI())
    assert result.record is not None, result.errors
    return result.record


def test_w2_sample_is_complete_and_urgent():
    record = extract("01_w2_extension_priya_nair.txt")

    assert record.is_complete
    assert record.tax_year == 2025
    assert record.urgency.level == "high"
    assert (record.street_address, record.city, record.state, record.zip_code) == (
        "2207 Barton Hills Dr, Apt 4C", "Austin", "TX", "78704"
    )
    assert record.drivers_license_number == "41827730"
    assert record.drivers_license_state == "TX"
    assert record.drivers_license_expires_on == date(2030, 3, 14)


def test_family_sample_reads_day_month_year_and_tax_situation():
    record = extract("02_joint_return_planning_daniel_okafor.txt")

    assert record.is_complete
    assert record.date_of_birth == date(1986, 7, 22)
    assert record.filing_status == "married_filing_jointly"
    assert record.number_of_dependents == 2
    assert record.deductions == ["mortgage_interest", "charitable", "childcare"]
    assert record.urgency.level == "low"


def test_messy_sample_is_missing_contact_address_and_license():
    record = extract("03_freelancer_unfiled_messy.txt")

    assert record.full_name == "Marcus Bell"
    assert (record.city, record.state) == ("Denver", "CO")
    assert record.missing_fields == [
        "phone", "date_of_birth", "street_address", "zip_code",
        "drivers_license_number", "drivers_license_state", "drivers_license_expires_on",
    ]
    assert record.income_sources == ["self_employment", "capital_gains"]


def test_pdf_form_with_separate_address_and_license_fields():
    record = extract("04_rideshare_estimated_tax_luis_ortega.pdf")

    assert record.is_complete
    assert (record.street_address, record.city, record.state, record.zip_code) == (
        "4410 E Camelback Rd, Unit 12", "Phoenix", "AZ", "85018"
    )
    assert record.drivers_license_issued_on == date(2021, 11, 2)
    assert record.deadline_date == date(2027, 1, 15)


def test_expired_license_is_captured_and_flagged():
    record = extract("05_widowed_retiree_harold_jensen.txt")

    assert record.is_complete
    assert record.license_expired
    assert record.income_sources == ["rental", "retirement"]


def test_irs_notice_sample_never_shows_the_ssn():
    record = extract("06_irs_notice_cp2000_angela_brooks.pdf")

    assert record.irs_notice_received is True
    assert record.urgency.level == "high"
    assert "123-45-6789" not in record.model_dump_json()


def test_unfiled_years_sample_uses_the_latest_year_and_needs_a_license():
    record = extract("10_unfiled_years_levy_notice_terrence_walker.pdf")

    assert record.tax_year == 2024
    assert record.missing_fields == ["drivers_license_number", "drivers_license_state", "drivers_license_expires_on"]


def test_prose_license_and_job_are_understood():
    record = extract("07_amend_head_of_household_jasmine_reed.txt")

    assert record.drivers_license_number == "058213447"
    assert record.drivers_license_state == "GA"
    assert (record.occupation, record.employer) == ("Medical assistant", "Peachtree Family Clinic")
    assert record.filing_status == "head_of_household"


def test_offline_checklist_follows_the_tax_profile():
    record = extract("06_irs_notice_cp2000_angela_brooks.pdf")
    checklist = OfflineAI().draft_checklist(record.tax_profile())

    assert "## IRS or state correspondence" in checklist
    assert "Form 1099-B" in checklist
    assert "for both spouses" in checklist
    assert "Angela" not in checklist
