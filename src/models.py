"""Data models.

`IntakeExtraction` is the contract between the AI and the rest of the pipeline: the model's JSON
must validate against it before anything downstream (form filling, follow-ups) happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

# Fields that must be present before a record may be submitted, with the client-friendly
# wording used in follow-up emails.
REQUIRED_FIELDS: dict[str, str] = {
    "full_name": "full legal name",
    "email": "email address",
    "phone": "phone number",
    "date_of_birth": "date of birth",
    "street_address": "street address",
    "city": "city",
    "state": "state",
    "zip_code": "ZIP code",
    "drivers_license_number": "driver's license (or state ID) number",
    "drivers_license_state": "state that issued your driver's license",
    "drivers_license_expires_on": "driver's license expiration date",
    "tax_year": "tax year you would like us to prepare",
}
RequiredField = Literal[
    "full_name", "email", "phone", "date_of_birth", "street_address", "city", "state", "zip_code",
    "drivers_license_number", "drivers_license_state", "drivers_license_expires_on", "tax_year",
]

MaritalStatus = Literal["single", "married", "divorced", "widowed", "separated"]
ContactMethod = Literal["email", "phone"]
FilingStatus = Literal[
    "single", "married_filing_jointly", "married_filing_separately", "head_of_household", "qualifying_surviving_spouse"
]
IncomeSource = Literal[
    "w2_wages", "self_employment", "interest_dividends", "capital_gains", "rental", "retirement", "unemployment",
    "other_income",
]
Deduction = Literal[
    "mortgage_interest", "charitable", "education", "student_loan_interest", "childcare", "medical",
    "retirement_contributions", "marketplace_insurance",
]

FILING_STATUSES: dict[str, str] = {
    "single": "Single",
    "married_filing_jointly": "Married filing jointly",
    "married_filing_separately": "Married filing separately",
    "head_of_household": "Head of household",
    "qualifying_surviving_spouse": "Qualifying surviving spouse",
}
# Values and the checkbox labels on the intake form.
INCOME_SOURCES: dict[str, str] = {
    "w2_wages": "W-2 wages",
    "self_employment": "Self-employment / 1099",
    "interest_dividends": "Interest and dividends",
    "capital_gains": "Stock, crypto or property sales",
    "rental": "Rental property income",
    "retirement": "Retirement, pension or Social Security",
    "unemployment": "Unemployment benefits",
    "other_income": "Other income",
}
DEDUCTIONS: dict[str, str] = {
    "mortgage_interest": "Mortgage interest / property tax",
    "charitable": "Charitable donations",
    "education": "Tuition or education expenses",
    "student_loan_interest": "Student loan interest",
    "childcare": "Child or dependent care",
    "medical": "Medical expenses",
    "retirement_contributions": "IRA or HSA contributions",
    "marketplace_insurance": "Marketplace health insurance (1095-A)",
}
assert set(FILING_STATUSES) == set(get_args(FilingStatus))
assert set(INCOME_SOURCES) == set(get_args(IncomeSource))
assert set(DEDUCTIONS) == set(get_args(Deduction))

_FILING_STATUS_ALIASES = {
    "mfj": "married_filing_jointly",
    "mfs": "married_filing_separately",
    "hoh": "head_of_household",
    "qss": "qualifying_surviving_spouse",
    "qualifying_widow": "qualifying_surviving_spouse",
    "qualifying_widower": "qualifying_surviving_spouse",
}

# Every client field on the intake form, in form order, with its form label. The labels are shared
# by the intake form, the form filler, and the run report. Urgency and notes are handled separately.
FORM_FIELDS: dict[str, str] = {
    "full_name": "Full legal name",
    "email": "Email address",
    "phone": "Phone number",
    "date_of_birth": "Date of birth",
    "marital_status": "Marital status",
    "preferred_contact_method": "Preferred contact method",
    "street_address": "Street address",
    "city": "City",
    "state": "State",
    "zip_code": "ZIP code",
    "drivers_license_number": "Driver's license / state ID number",
    "drivers_license_state": "Issuing state",
    "drivers_license_issued_on": "License issue date",
    "drivers_license_expires_on": "License expiration date",
    "tax_year": "Tax year",
    "filing_status": "Filing status",
    "number_of_dependents": "Number of dependents",
    "occupation": "Occupation",
    "employer": "Employer",
    "income_sources": "Income sources",
    "deductions": "Deductions and credits",
    "irs_notice_received": "IRS or state notice received",
    "deadline_date": "Deadline",
    "consultation_requested": "Consultation requested",
    "request_summary": "Request summary",
}
OPTIONAL_FIELDS = tuple(name for name in FORM_FIELDS if name not in REQUIRED_FIELDS)
assert set(REQUIRED_FIELDS) <= set(FORM_FIELDS)

_TEXT_FIELDS = (
    "full_name", "email", "phone", "street_address", "city", "zip_code", "drivers_license_number", "occupation",
    "employer", "request_summary",
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_LICENSE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 -]*[A-Z0-9]$")
_PLACEHOLDERS = {"null", "none", "n/a", "na", "unknown", "not provided", "not stated"}
_EARLIEST_DATE = date(1900, 1, 1)
_LATEST_DATE_YEARS = 30
_EARLIEST_TAX_YEAR = 1990
_MAX_DEPENDENTS = 20


def mask_identifier(value: str) -> str:
    """Show only the last four characters of an ID number (for reports and screenshots)."""
    compact = re.sub(r"[\s-]", "", value)
    return "•" * max(len(compact) - 4, 0) + compact[-4:]


class Urgency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["low", "medium", "high"]
    reason: str = Field(description="One short sentence explaining the level, citing the email.")

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("urgency reason must not be empty")
        return value


@dataclass(frozen=True)
class TaxProfile:
    """The non-identifying facts the document-checklist step is allowed to see."""

    tax_year: int
    state: str | None
    marital_status: str | None
    filing_status: str | None
    number_of_dependents: int | None
    income_sources: tuple[str, ...]
    deductions: tuple[str, ...]
    irs_notice_received: bool | None

    def describe(self) -> str:
        def stated(value: Any) -> str:
            return "not stated" if value is None else str(value)

        notice = {True: "yes", False: "no", None: "not stated"}[self.irs_notice_received]
        return "\n".join(
            [
                f"Tax year: {self.tax_year}",
                f"State of residence: {stated(self.state)}",
                f"Marital status: {stated(self.marital_status)}",
                f"Filing status: {FILING_STATUSES.get(self.filing_status or '', 'not stated')}",
                f"Dependents: {stated(self.number_of_dependents)}",
                "Income sources: " + (", ".join(INCOME_SOURCES[s] for s in self.income_sources) or "not stated"),
                "Deductions and credits mentioned: "
                + (", ".join(DEDUCTIONS[d] for d in self.deductions) or "none mentioned"),
                f"IRS or state notice received: {notice}",
            ]
        )


class IntakeExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(description="The taxpayer's full legal name; null if not stated.")
    email: str | None = Field(description="The client's email address; null if not stated.")
    phone: str | None = Field(description="The client's phone number as written; null if not stated.")
    date_of_birth: date | None = Field(description="YYYY-MM-DD; null unless the full date is stated.")
    street_address: str | None = Field(description="Street address of the client's home, including any unit.")
    city: str | None = Field(description="City of the client's home address; null if not stated.")
    state: str | None = Field(description="Two-letter USPS code of the client's home state, e.g. 'TX'.")
    zip_code: str | None = Field(description="5-digit ZIP or ZIP+4 of the client's home address.")
    drivers_license_number: str | None = Field(
        description="Driver's license or state ID number exactly as written; null if not stated."
    )
    drivers_license_state: str | None = Field(description="Two-letter code of the state that issued the license.")
    drivers_license_expires_on: date | None = Field(description="YYYY-MM-DD expiration date of the license.")
    tax_year: int | None = Field(description="Four-digit tax year the client wants prepared; null if unclear.")
    urgency: Urgency
    missing_fields: list[RequiredField] = Field(description="Required fields returned as null.")

    # Optional details (see OPTIONAL_FIELDS). Null or empty whenever the email does not state them.
    marital_status: MaritalStatus | None = Field(default=None, description="Null unless stated or clearly implied.")
    preferred_contact_method: ContactMethod | None = Field(
        default=None, description="How the client asks to be contacted; null if not stated."
    )
    drivers_license_issued_on: date | None = Field(default=None, description="YYYY-MM-DD issue date of the license.")
    filing_status: FilingStatus | None = Field(default=None, description="Only if the client names their filing status.")
    number_of_dependents: int | None = Field(default=None, description="Dependents the client says they will claim.")
    occupation: str | None = Field(default=None, description="Job title or profession; null if not stated.")
    employer: str | None = Field(default=None, description="Current employer's name; null if not stated.")
    income_sources: list[IncomeSource] = Field(default_factory=list, description="Kinds of income mentioned.")
    deductions: list[Deduction] = Field(default_factory=list, description="Deductions and credits mentioned.")
    irs_notice_received: bool | None = Field(
        default=None, description="True if the client received an IRS or state tax notice; null if not mentioned."
    )
    deadline_date: date | None = Field(
        default=None, description="YYYY-MM-DD of the earliest specific deadline the client mentions."
    )
    consultation_requested: bool | None = Field(
        default=None, description="True if the client asks for a consultation or call; null if not mentioned."
    )
    request_summary: str | None = Field(
        default=None, description="One or two neutral sentences summarizing what the client is asking for."
    )

    @field_validator(
        *_TEXT_FIELDS, "date_of_birth", "state", "drivers_license_state", "drivers_license_expires_on", "tax_year",
        "marital_status", "preferred_contact_method", "drivers_license_issued_on", "filing_status",
        "number_of_dependents", "irs_notice_received", "deadline_date", "consultation_requested",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value or value.lower() in _PLACEHOLDERS:
                return None
        return value

    @field_validator("marital_status", "preferred_contact_method", mode="before")
    @classmethod
    def _lowercase_choice(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @field_validator("filing_status", mode="before")
    @classmethod
    def _normalize_filing_status(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        key = re.sub(r"[\s-]+", "_", value.strip().lower())
        return _FILING_STATUS_ALIASES.get(key, key)

    @field_validator("state", "drivers_license_state", mode="before")
    @classmethod
    def _state_code(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.upper() in US_STATES:
            return text.upper()
        if text.lower() in _STATE_BY_NAME:
            return _STATE_BY_NAME[text.lower()]
        raise ValueError(f"not a U.S. state: {value!r}")

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.match(value):
            raise ValueError(f"not a valid email address: {value!r}")
        return value

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, value: str | None) -> str | None:
        if value is not None and not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError(f"phone number should have 7-15 digits: {value!r}")
        return value

    @field_validator("zip_code")
    @classmethod
    def _valid_zip(cls, value: str | None) -> str | None:
        if value is not None and not _ZIP_RE.match(value):
            raise ValueError(f"ZIP code should be 5 digits or ZIP+4: {value!r}")
        return value

    @field_validator("drivers_license_number")
    @classmethod
    def _valid_license(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.upper()
        compact = re.sub(r"[\s-]", "", value)
        if not _LICENSE_RE.match(value) or not 4 <= len(compact) <= 20 or not re.search(r"\d", compact):
            raise ValueError(f"not a plausible driver's license number: {value!r}")
        return value

    @field_validator("date_of_birth", "drivers_license_issued_on")
    @classmethod
    def _plausible_past_date(cls, value: date | None) -> date | None:
        if value is not None and not _EARLIEST_DATE <= value <= date.today():
            raise ValueError(f"not a plausible past date: {value.isoformat()}")
        return value

    @field_validator("drivers_license_expires_on", "deadline_date")
    @classmethod
    def _plausible_date(cls, value: date | None) -> date | None:
        latest = date(date.today().year + _LATEST_DATE_YEARS, 12, 31)
        if value is not None and not _EARLIEST_DATE <= value <= latest:
            raise ValueError(f"not a plausible date: {value.isoformat()}")
        return value

    @field_validator("tax_year")
    @classmethod
    def _plausible_tax_year(cls, value: int | None) -> int | None:
        if value is not None and not _EARLIEST_TAX_YEAR <= value <= date.today().year:
            raise ValueError(f"not a plausible tax year: {value}")
        return value

    @field_validator("number_of_dependents")
    @classmethod
    def _plausible_dependents(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= _MAX_DEPENDENTS:
            raise ValueError(f"number of dependents should be 0-{_MAX_DEPENDENTS}: {value}")
        return value

    @field_validator("income_sources", "deductions", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        return [] if value is None else value

    @model_validator(mode="after")
    def _normalize(self) -> IntakeExtraction:
        """Code, not the model, has the final say on what is missing.

        Any required field that is empty is added to missing_fields even if the model said the
        record was complete; anything the model flagged is kept, so staff confirm it with the client.
        Checkbox lists are de-duplicated into form order so they compare cleanly after submission.
        """
        flagged = set(self.missing_fields)
        flagged |= {name for name in REQUIRED_FIELDS if getattr(self, name) is None}
        self.missing_fields = [name for name in REQUIRED_FIELDS if name in flagged]
        self.income_sources = [s for s in INCOME_SOURCES if s in self.income_sources]
        self.deductions = [d for d in DEDUCTIONS if d in self.deductions]
        return self

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    @property
    def missing_field_labels(self) -> list[str]:
        return [REQUIRED_FIELDS[name] for name in self.missing_fields]

    @property
    def license_expired(self) -> bool:
        return self.drivers_license_expires_on is not None and self.drivers_license_expires_on < date.today()

    def display_value(self, name: str) -> str | None:
        """A client field as display text (None when not stated). ID numbers are masked."""
        value = getattr(self, name)
        if value is None or value == []:
            return None
        if name == "drivers_license_number":
            return mask_identifier(value)
        if name == "drivers_license_expires_on":
            return value.isoformat() + (" (expired)" if self.license_expired else "")
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, date):
            return value.isoformat()
        if name == "income_sources":
            return ", ".join(INCOME_SOURCES[v] for v in value)
        if name == "deductions":
            return ", ".join(DEDUCTIONS[v] for v in value)
        if name == "filing_status":
            return FILING_STATUSES[value]
        if name in ("marital_status", "preferred_contact_method"):
            return value.capitalize()
        return str(value)

    def client_details(self) -> list[tuple[str, str, str | None]]:
        """Every client field as (field name, label, display value or None if not stated)."""
        rows = [(name, label, self.display_value(name)) for name, label in FORM_FIELDS.items()]
        rows.append(("urgency", "Urgency", self.urgency.level.capitalize()))
        return rows

    def tax_profile(self) -> TaxProfile:
        if self.tax_year is None:
            raise ValueError("a tax profile needs a tax year")
        return TaxProfile(
            tax_year=self.tax_year,
            state=self.state,
            marital_status=self.marital_status,
            filing_status=self.filing_status,
            number_of_dependents=self.number_of_dependents,
            income_sources=tuple(self.income_sources),
            deductions=tuple(self.deductions),
            irs_notice_received=self.irs_notice_received,
        )


class Outcome(str, Enum):
    SUBMITTED = "Submitted"
    FOLLOW_UP = "Follow-up drafted"
    NEEDS_REVIEW = "Needs human review"
    ERROR = "Error"


@dataclass
class RecordResult:
    """What happened to one intake email during a run."""

    source_file: str
    outcome: Outcome = Outcome.ERROR
    extraction: IntakeExtraction | None = None
    extraction_attempts: int = 0
    intake_id: str | None = None
    verified: bool | None = None  # None = not submitted, so nothing to verify
    verification_detail: str | None = None
    redacted: dict[str, int] = field(default_factory=dict)  # kind of sensitive number -> count removed
    artifacts: list[Path] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0
