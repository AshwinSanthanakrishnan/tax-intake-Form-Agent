"""Run summary counts and Markdown report rendering."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.models import IntakeExtraction, Outcome, RecordResult, Urgency
from src.redaction import SSN
from src.report_pdf import render_report_html
from src.reporting import RunInfo, format_duration, render_report, summarize

OUTPUTS = Path("/tmp/outputs")
INFO = RunInfo(started_at=datetime(2026, 10, 19, 9, 30), ai_engine="test engine", browser_mode="headless")


def make_record(name="Test Client", **overrides):
    values = dict(
        full_name=name,
        email="client@example.com",
        phone="555-010-0000",
        date_of_birth="1990-01-31",
        street_address="100 Main St",
        city="Austin",
        state="TX",
        zip_code="78701",
        drivers_license_number="41827730",
        drivers_license_state="TX",
        drivers_license_expires_on="2030-01-31",
        tax_year=2025,
        urgency=Urgency(level="high", reason="Extension deadline is this week."),
        missing_fields=[],
    )
    values.update(overrides)
    return IntakeExtraction(**values)


def make_results():
    return [
        RecordResult(
            "a.txt",
            Outcome.SUBMITTED,
            make_record("Ana One"),
            1,
            intake_id="TAX-20261019-AAAA",
            verified=True,
            verification_detail="ok",
            artifacts=[OUTPUTS / "screenshots" / "TAX-20261019-AAAA.png"],
            duration_s=2.5,
        ),
        RecordResult("b.txt", Outcome.SUBMITTED, make_record("Ben Two"), 1, verified=False, verification_detail="no ID"),
        RecordResult("c.txt", Outcome.FOLLOW_UP, make_record("Cy Three", date_of_birth=None), 1, redacted={SSN: 1}),
        RecordResult("d.txt", Outcome.NEEDS_REVIEW, None, 2, error="invalid twice"),
        RecordResult("e.txt", Outcome.ERROR, None, 0, error="read: InputError: e.txt is empty"),
    ]


def test_summary_counts_every_outcome():
    summary = summarize(make_results(), duration_s=12.0)

    assert summary.processed == 5
    assert summary.submitted == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.follow_ups == 1
    assert summary.needs_review == 1
    assert summary.errors == 1
    assert summary.redacted == 1
    assert summary.needs_attention


def test_submitted_record_without_a_verification_result_counts_as_failed():
    results = [RecordResult("a.txt", Outcome.SUBMITTED, make_record(), 1, verified=None)]

    assert summarize(results, 1.0).failed == 1


def test_follow_ups_and_human_review_alone_do_not_need_attention():
    results = [
        RecordResult("a.txt", Outcome.SUBMITTED, make_record(), 1, verified=True),
        RecordResult("b.txt", Outcome.FOLLOW_UP, make_record(phone=None), 1),
        RecordResult("c.txt", Outcome.NEEDS_REVIEW, None, 2),
    ]

    assert not summarize(results, 1.0).needs_attention


def test_report_contains_summary_rows_and_relative_links():
    results = make_results()
    report = render_report(results, summarize(results, 12.0), INFO, report_dir=OUTPUTS)

    assert "| Records processed | 5 |" in report
    assert "| Verification PASS | 1 |" in report
    assert "| Verification FAIL | 1 |" in report
    assert "| Emails with an SSN or bank number (redacted) | 1 |" in report
    assert "**Attention needed:** 1 verification failure(s), 1 error(s)." in report
    assert "| 1 | a.txt | Ana One | 2025 | High | Submitted | TAX-20261019-AAAA | PASS | 2.5 s |" in report
    assert "| 2 | b.txt | Ben Two | 2025 | High | Submitted | - | FAIL |" in report
    assert "| 3 | c.txt | Cy Three | 2025 | High | Follow-up drafted | - | n/a |" in report
    assert "| 4 | d.txt | - | - | - | Needs human review | - | n/a |" in report
    assert "[TAX-20261019-AAAA.png](screenshots/TAX-20261019-AAAA.png)" in report
    assert "- **Missing fields:** date of birth" in report
    assert "- **Redacted before AI:** SSN/ITIN (1)" in report
    assert "Review and send 1 follow-up draft(s)" in report
    assert "Remind them to use a secure channel" in report


def test_report_never_shows_the_full_license_number():
    results = make_results()
    report = render_report(results, summarize(results, 12.0), INFO, report_dir=OUTPUTS)
    html = render_report_html(results, summarize(results, 12.0), INFO)

    assert "41827730" not in report
    assert "41827730" not in html
    assert "| Driver's license / state ID number | ••••7730 |" in report


def test_report_for_a_clean_run():
    results = [RecordResult("a.txt", Outcome.SUBMITTED, make_record(), 1, verified=True, intake_id="TAX-20261019-AAAA")]
    report = render_report(results, summarize(results, 3.0), INFO, report_dir=OUTPUTS)

    assert "**All submitted records verified.**" in report
    assert "Attention needed" not in report


def test_report_with_no_records():
    report = render_report([], summarize([], 0.1), INFO, report_dir=OUTPUTS)

    assert "| Records processed | 0 |" in report
    assert "No intake emails were found." in report


def test_table_cells_escape_pipes():
    results = [RecordResult("a.txt", Outcome.FOLLOW_UP, make_record("Ana | One", phone=None), 1)]
    report = render_report(results, summarize(results, 1.0), INFO, report_dir=OUTPUTS)

    assert "Ana \\| One" in report


def test_report_lists_client_details():
    results = [
        RecordResult(
            "a.txt",
            Outcome.SUBMITTED,
            make_record(occupation="Data analyst", income_sources=["w2_wages", "rental"]),
            1,
            verified=True,
        )
    ]
    report = render_report(results, summarize(results, 1.0), INFO, report_dir=OUTPUTS)

    assert "| Occupation | Data analyst |" in report
    assert "| Income sources | W-2 wages, Rental property income |" in report
    assert "| Filing status |" not in report, "unstated details are left out of the Markdown table"


def test_pdf_html_has_every_record_and_flags_missing_fields():
    results = make_results()
    html = render_report_html(results, summarize(results, 12.0), INFO)

    for number, name in enumerate(("Ana One", "Ben Two", "Cy Three"), start=1):
        assert f"<h3>{number}. {name}</h3>" in html
    assert "<h3>4. Unidentified client</h3>" in html
    assert '<dt>Date of birth</dt><dd><span class="missing">Missing</span></dd>' in html
    assert "Attention needed: 1 verification failure(s), 1 error(s)." in html
    assert "Review and send 1 follow-up draft(s)" in html


def test_pdf_html_escapes_client_text():
    results = [RecordResult("a.txt", Outcome.FOLLOW_UP, make_record("<script>x</script>", phone=None), 1)]
    html = render_report_html(results, summarize(results, 1.0), INFO)

    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_format_duration():
    assert format_duration(4.26) == "4.3 s"
    assert format_duration(75) == "1 min 15 s"
