"""Step 4: fill and submit the intake form with Playwright."""

from __future__ import annotations

import logging

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from .models import DEDUCTIONS, FORM_FIELDS, INCOME_SOURCES, IntakeExtraction

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 10_000
SELECT_FIELDS = {
    "state", "drivers_license_state", "marital_status", "preferred_contact_method", "filing_status",
    "irs_notice_received", "consultation_requested",
}
# Checkbox groups: field name -> {value: checkbox label}
CHECKBOX_FIELDS = {"income_sources": INCOME_SOURCES, "deductions": DEDUCTIONS}


def form_values(record: IntakeExtraction) -> dict[str, str | list[str]]:
    """Every form field as the browser submits it ("" or [] for anything not stated)."""
    values: dict[str, str | list[str]] = {}
    for name in FORM_FIELDS:
        value = getattr(record, name)
        if name in CHECKBOX_FIELDS:
            values[name] = list(value)
        elif value is None:
            values[name] = ""
        elif isinstance(value, bool):
            values[name] = "yes" if value else "no"
        else:
            values[name] = value.isoformat() if hasattr(value, "isoformat") else str(value)
    values["urgency"] = record.urgency.level
    return values


class BrowserSession:
    """One Chromium instance for the whole run, launched on first use.

    A single page is reused across records so a headed demo stays in one window.
    """

    def __init__(self, *, headless: bool, slow_mo_ms: int) -> None:
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    def page(self) -> Page:
        if self._browser is None:
            self._launch()
        assert self._browser is not None
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page(viewport={"width": 1280, "height": 900})
            self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        return self._page

    def reset_page(self) -> None:
        """Discard the current page (e.g. after an error) so the next record starts clean."""
        if self._page is not None and not self._page.is_closed():
            self._page.close()
        self._page = None

    def pause_for_viewer(self, ms: int = 1500) -> None:
        """In a headed run, hold on the current screen long enough to see it in a recording."""
        if not self.headless and self._page is not None and not self._page.is_closed():
            self._page.wait_for_timeout(ms)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def _launch(self) -> None:
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        mode = "headless" if self.headless else f"headed, slow-mo {self.slow_mo_ms} ms"
        log.info("Launched Chromium (%s)", mode)

    def __enter__(self) -> BrowserSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def fill_intake_form(page: Page, form_url: str, record: IntakeExtraction, notes: str) -> None:
    if not record.is_complete:
        raise ValueError(f"refusing to submit an incomplete record (missing: {', '.join(record.missing_fields)})")

    page.goto(form_url)
    # Fields are found by their visible label, the way a person would fill the form. Only fields
    # the email stated are touched; the rest stay blank.
    values = form_values(record)
    for name, label in FORM_FIELDS.items():
        value = values[name]
        if not value:
            continue
        if name in CHECKBOX_FIELDS:
            for item in value:
                page.get_by_label(CHECKBOX_FIELDS[name][item], exact=True).check()
            continue
        field = page.get_by_label(label, exact=True)
        if name in SELECT_FIELDS:
            field.select_option(value)
        else:
            field.fill(value)

    page.get_by_label("Urgency", exact=True).select_option(record.urgency.level)
    page.get_by_label("Notes", exact=True).fill(notes)
    page.get_by_role("button", name="Submit intake").click()
    log.info("Submitted intake form for %s", record.full_name)
