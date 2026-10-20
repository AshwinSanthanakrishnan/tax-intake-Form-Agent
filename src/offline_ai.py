"""Rule-based stand-in for the AI, used with --offline.

It lets the browser, verification, and reporting steps be demoed and smoke-tested without an
API key. It is deliberately simple (keywords and regexes, no AI), and its output goes through
exactly the same Pydantic validation as the model's. Nothing here is meant for production use.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from email.utils import parseaddr

from .models import DEDUCTIONS, FILING_STATUSES, INCOME_SOURCES, REQUIRED_FIELDS, US_STATES, IntakeExtraction, TaxProfile

_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}
_STATE_NAMES_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, US_STATES.values()), key=len, reverse=True)) + r")\b")
_STATE_CODE_RE = re.compile(r"\b([A-Z]{2})\b")

_PHONE_RE = re.compile(r"(?<![\w])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PROSE_NAME_RE = re.compile(r"\b(?:my name is|name is|this is)\s+([a-z][a-z'-]+(?:[ \t]+[a-z][a-z'-]+){1,2})", re.I)
_DOB_RE = re.compile(r"\b(?:date of birth|dob|born(?: on)?)\s*[:\-]?\s*([A-Za-z0-9 ,/.-]{6,30})", re.I)
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d")
_DATE_IN_TEXT_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]+ \d{1,2}, \d{4})\b")
_ADDRESS_RE = re.compile(
    r"^(?P<street>\d+[^,\n]*(?:,\s*(?:apt|unit|suite|ste|#)[^,\n]*)?),\s*(?P<city>[A-Za-z .'-]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z ]+?)\s+(?P<zip>\d{5}(?:-\d{4})?)$",
    re.I,
)
_LOCATION_RE = re.compile(
    r"\b(?:living|working|based|live|reside|residing) in ([A-Z][a-z]+(?: [A-Z][a-z]+)?), "
    r"([A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)?)\b"
)
_LICENSE_LINE_RE = re.compile(r"^.*\b(?:driver'?s?|drivers) licen[cs]e\b.*$", re.I | re.M)
_LICENSE_NUMBER_RE = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,19}\b")
_TAX_YEAR_RE = re.compile(r"\b(20\d{2})\s+(?:federal\s+|joint\s+|tax\s+|income\s+tax\s+)?(?:returns?|taxes)\b", re.I)
_HIGH_RE = re.compile(
    r"\b(urgent|asap|as soon as possible|deadline is|extension deadline|extended deadline|past due|"
    r"penalt(?:y|ies)|garnish\w*|levy|lien|audit|respond by|final notice)\b"
)
_LOW_RE = re.compile(r"\b(no (?:big )?rush|no hurry|not urgent|just curious|general question|no pressure)\b")
_MONTH_DATE = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
_DEADLINE_RE = re.compile(rf"\b(?:respond by|(?:is )?due(?: by| on)?|deadline is|deadline of)\s+({_MONTH_DATE})")
# Case-sensitive on purpose: the employer must be a capitalized name ("at Peachtree Family Clinic").
_JOB_RE = re.compile(
    r"\b(?:[Ii] work as|[Ii]'?m|[Ii] am|job as)\s+an?\s+([a-z][a-z -]{2,40}?)\s+(?:at|with|for)\s+"
    r"([A-Z][\w&.'-]*(?:[ \t]+[A-Z][\w&.'-]*)*)"
)
_DEPENDENTS_RE = re.compile(r"\b(\d+|one|two|three|four|five|six)\s+(?:dependents?|kids|children|daughters?|sons?)\b", re.I)
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

_INCOME_RULES: dict[str, str] = {
    "w2_wages": r"\bw-?2\b|\bsalary\b|\bwages\b|\bpaychecks?\b",
    "self_employment": (
        r"\b1099(?:-(?:nec|k|misc))?\b(?!-(?:r|int|div|b|g))|\bfreelanc\w*|\bself-employed\b|\bgig\b|\brideshare\b"
        r"|\betsy\b|\bhandyman\b|\bmy own business\b|\bside business\b|\bschedule c\b"
    ),
    "interest_dividends": r"\b1099-(?:int|div)\b|\binterest income\b|\bdividends?\b|\bbank interest\b",
    "capital_gains": (
        r"\b1099-b\b|\bcrypto\w*|\bbitcoin\b|\bcapital gains?\b|\bbrokerage (?:account )?sales?\b"
        r"|\bsold\b[^.\n]{0,30}\b(?:stocks?|shares|crypto|bitcoin|home|house|property)\b"
    ),
    "rental": r"\brental (?:property|income)\b|\brent(?:ed)? (?:it )?out\b|\btenants?\b|\brent from\b|\bduplex\b",
    "retirement": (
        r"\bpensions?\b|\bsocial security (?:benefits|income)\b|\b1099-r\b|\bssa-1099\b|\bira distributions?\b"
        r"|\brequired minimum distributions?\b|\brmds?\b"
    ),
    "unemployment": r"\bunemployment\b|\b1099-g\b",
}
_DEDUCTION_RULES: dict[str, str] = {
    "mortgage_interest": r"\bmortgage\b|\bproperty tax(?:es)?\b|\b1098\b(?!-)",
    "charitable": r"\bcharit\w*|\bdonat\w*|\bgive to (?:our|my) church\b|\btith\w*",
    "education": r"\btuition\b|\b1098-t\b|\bcollege expenses\b",
    "student_loan_interest": r"\bstudent loan",
    "childcare": r"\bday ?care\b|\bchild ?care\b|\bdependent care\b|\bafter-school care\b",
    "medical": r"\bmedical (?:bills|expenses)\b",
    "retirement_contributions": (
        r"\bhsa\b|\bcontribut\w* to (?:an? |my |our )?(?:roth |traditional )?ira\b|\bira contributions?\b"
    ),
    "marketplace_insurance": r"\b1095-a\b|\bmarketplace\b|\bhealthcare\.gov\b",
}
assert set(_INCOME_RULES) <= set(INCOME_SOURCES) and set(_DEDUCTION_RULES) == set(DEDUCTIONS)
_IRS_NOTICE_RE = re.compile(
    r"\b(?:irs|state|department of revenue|franchise tax board)\b[^.\n]{0,60}\b(?:notice|letter)\b"
    r"|\bcp\d{2,4}\b|\bnotice of intent\b"
)
_FILING_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\bmarried filing jointly\b|\bfile jointly\b", "married_filing_jointly"),
    (r"\bmarried filing separately\b|\bfile separately\b", "married_filing_separately"),
    (r"\bhead of household\b", "head_of_household"),
    (r"\bqualifying (?:surviving spouse|widow(?:er)?)\b", "qualifying_surviving_spouse"),
)
_MARITAL = {"single", "married", "divorced", "widowed", "separated"}


class OfflineAI:
    name = "Offline rule-based stand-in (no AI calls)"

    def extract(self, email_text: str, previous_error: str | None = None) -> str:
        headers, body = _split_email(email_text)
        from_name, from_address = parseaddr(headers.get("from", ""))
        data: dict[str, object] = {
            "full_name": _full_name(from_name, body),
            "email": from_address if "@" in from_address else _first_match(_EMAIL_RE, body),
            "phone": _labeled(body, "phone", "telephone", "cell", "mobile") or _first_match(_PHONE_RE, body),
            "date_of_birth": _date_of_birth(body),
            **_address(body),
            **_drivers_license(body),
            "tax_year": _tax_year(body),
            "urgency": _labeled_urgency(body) or _urgency(body),
        }
        data["missing_fields"] = [name for name in REQUIRED_FIELDS if data[name] is None]
        data.update(_optional_details(headers, body))
        return json.dumps(data)

    def draft_followup(self, email_text: str, record: IntakeExtraction) -> str:
        first_name = record.full_name.split()[0] if record.full_name else "there"
        about = f"your {record.tax_year} tax return" if record.tax_year else "your taxes"
        items = "\n".join(f"- {label}" for label in record.missing_field_labels)
        return (
            "Subject: Your inquiry - a few more details needed\n\n"
            f"Hi {first_name},\n\n"
            f"Thank you for contacting our office about {about}. Before we can open an intake "
            "record for you, could you please send us the following:\n\n"
            f"{items}\n\n"
            "You can simply reply to this email with the details. Please do not include your Social "
            "Security number or bank details; we collect those separately.\n\n"
            "Best regards,\nClient Intake Team"
        )

    def draft_checklist(self, profile: TaxProfile) -> str:
        return _checklist(profile)

    def usage_summary(self) -> str | None:
        return None


# --- extraction helpers -------------------------------------------------------------------


def _split_email(text: str) -> tuple[dict[str, str], str]:
    """Split the top header block from the body. If no email headers exist, entire text is body."""
    head, sep, rest = text.partition("\n\n")
    headers = {}
    if "from:" in head.lower() or "to:" in head.lower():
        for line in head.splitlines():
            key, s, value = line.partition(":")
            if s:
                headers[key.strip().lower()] = value.strip()
        body = rest
    else:
        body = text
    return headers, body


def _labeled(body: str, *labels: str) -> str | None:
    """The value after "Label:" at the start of a line (optionally a "- " bullet)."""
    names = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"^[ \t]*(?:-[ \t]*)?(?:{names})[ \t]*:[ \t]*(.+)$", body, re.I | re.M)
    return match.group(1).strip() if match else None


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def _parse_date(text: str | None) -> str | None:
    if not text:
        return None
    candidate = text.strip(" ,.-")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    match = _DATE_IN_TEXT_RE.search(candidate)
    return _parse_date(match.group(0)) if match and match.group(0) != candidate else None


def _state_code(text: str | None) -> str | None:
    if not text:
        return None
    if text.strip().upper() in US_STATES:
        return text.strip().upper()
    if match := _STATE_NAMES_RE.search(text):
        return _STATE_BY_NAME[match.group(1).lower()]
    return next((code for code in _STATE_CODE_RE.findall(text) if code in US_STATES), None)


def _full_name(from_name: str, body: str) -> str | None:
    if len(from_name.split()) >= 2:
        return from_name.strip()
    name = _labeled(body, "full legal name", "full name", "client name", "taxpayer name", "name")
    if name is None and (match := _PROSE_NAME_RE.search(body)):
        name = match.group(1)
    if name is None:
        return None
    return name.title() if name.islower() else name


def _date_of_birth(body: str) -> str | None:
    for match in _DOB_RE.finditer(body):
        if parsed := _parse_date(match.group(1)):
            return parsed
    return None


def _address(body: str) -> dict[str, str | None]:
    parts: dict[str, str | None] = {"street_address": None, "city": None, "state": None, "zip_code": None}
    value = _labeled(body, "home address", "mailing address", "street address", "address")
    if value and (match := _ADDRESS_RE.match(value)):
        parts.update(
            street_address=match["street"].strip(),
            city=match["city"].strip(),
            state=_state_code(match["state"]),
            zip_code=match["zip"],
        )
    elif value and re.match(r"\d", value):
        parts["street_address"] = value
    parts["city"] = parts["city"] or _labeled(body, "city")
    parts["state"] = parts["state"] or _state_code(_labeled(body, "state"))
    parts["zip_code"] = parts["zip_code"] or _labeled(body, "zip", "zip code", "postal code")
    if parts["city"] is None and (match := _LOCATION_RE.search(body)):
        parts["city"], parts["state"] = match.group(1), parts["state"] or _state_code(match.group(2))
    return parts


def _drivers_license(body: str) -> dict[str, str | None]:
    number = _labeled(body, "driver's license number", "drivers license number", "license number")
    state = _state_code(_labeled(body, "issuing state", "license state"))
    expires = _parse_date(_labeled(body, "license expiration", "license expiration date", "license expires"))
    issued = _parse_date(_labeled(body, "license issue date", "license issued"))

    if match := _LICENSE_LINE_RE.search(body):
        line = match.group(0)
        without_dates = _DATE_IN_TEXT_RE.sub(" ", line)
        if number is None and (found := _LICENSE_NUMBER_RE.search(without_dates)):
            number = found.group(0)
        state = state or _state_code(re.sub(r"(?i)\bdriver'?s?\b|\blicen[cs]e\b", " ", without_dates))
        if expires is None and (found := re.search(r"\bexpir\w*(?: date)?(?: on)?[:\s]+(.+?)(?:[.;]|$)", line, re.I)):
            expires = _parse_date(found.group(1))
        if issued is None and (found := re.search(r"\bissued(?: on)?[:\s]+(.+?)(?:,|[.;]|$)", line, re.I)):
            issued = _parse_date(found.group(1))
    if number is None:  # without a number, nothing else on the line describes a license
        state = expires = issued = None
    return {
        "drivers_license_number": number,
        "drivers_license_state": state,
        "drivers_license_expires_on": expires,
        "drivers_license_issued_on": issued,
    }


def _tax_year(body: str) -> int | None:
    labeled = _labeled(body, "tax year", "tax years", "tax years needed")
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", labeled or "")]
    years = years or [int(y) for y in _TAX_YEAR_RE.findall(body)]
    years = [y for y in years if y <= date.today().year]
    return max(years) if years else None


def _urgency(body: str) -> dict[str, str]:
    text = body.lower()
    if match := _HIGH_RE.search(text):
        return {"level": "high", "reason": f"Email mentions '{match.group(0)}' (keyword rule)."}
    if match := _LOW_RE.search(text):
        return {"level": "low", "reason": f"Email says '{match.group(0)}' (keyword rule)."}
    return {"level": "medium", "reason": "No deadline or time-pressure keywords found (keyword rule)."}


def _labeled_urgency(body: str) -> dict[str, str] | None:
    value = _labeled(body, "urgency")
    match = re.match(r"(low|medium|high)\b[\s.:-]*(.*)", value or "", re.I)
    if not match:
        return None
    reason = match.group(2).strip() or f"Client marked urgency as {match.group(1).lower()}."
    return {"level": match.group(1).lower(), "reason": f"{reason[0].upper()}{reason[1:]} (stated by client)."}


def _optional_details(headers: dict[str, str], body: str) -> dict[str, object]:
    text = body.lower()

    marital = (_labeled(body, "marital status") or "").lower().split()[0:1]
    marital = marital[0] if marital and marital[0] in _MARITAL else None
    if marital is None:
        if re.search(r"\bsingle (?:mom|mother|dad|father|parent)\b|\bi'?m single\b", text):
            marital = "single"
        elif re.search(r"\bmy (?:husband|wife|spouse)\b|\bwe got married\b|\bi am married\b", text):
            marital = "married"

    filing = (_labeled(body, "filing status") or "").strip().lower()
    filing = re.sub(r"[\s-]+", "_", filing) if filing else None
    if filing not in FILING_STATUSES:
        filing = next((value for pattern, value in _FILING_PHRASES if re.search(pattern, text)), None)

    dependents = None
    labeled_dependents = _labeled(body, "dependents", "number of dependents")
    if labeled_dependents and (match := re.match(r"(\d+)", labeled_dependents)):
        dependents = int(match.group(1))
    elif match := _DEPENDENTS_RE.search(body):
        word = match.group(1).lower()
        dependents = int(word) if word.isdigit() else _NUMBER_WORDS[word]

    occupation = _labeled(body, "occupation", "job title")
    employer = _labeled(body, "employer", "company")
    if match := _JOB_RE.search(body):
        occupation = occupation or match.group(1)[0].upper() + match.group(1)[1:]
        employer = employer or match.group(2)

    contact = (_labeled(body, "preferred contact", "preferred contact method") or "").lower() or None
    if contact not in (None, "email", "phone"):
        contact = None
    if contact is None and re.search(
        r"\bemail is the best way\b|\bprefer(?:red)? (?:to be contacted by )?email\b|\breply by email\b", text
    ):
        contact = "email"
    elif contact is None and re.search(
        r"\b(?:phone|call) is the best way\b|\bprefer(?:red)? (?:to be contacted by |a )?(?:call|phone)\b", text
    ):
        contact = "phone"

    deadline = _parse_date(_labeled(body, "response due", "respond by", "deadline"))
    if deadline is None and (match := _DEADLINE_RE.search(body)):
        deadline = _parse_date(match.group(1))

    consultation = re.search(
        r"\bconsultation\b|\b(?:set up|schedule) (?:a )?(?:quick |short )?call\b", text
    )
    return {
        "marital_status": marital,
        "preferred_contact_method": contact,
        "filing_status": filing,
        "number_of_dependents": dependents,
        "occupation": occupation,
        "employer": employer,
        "income_sources": [name for name, pattern in _INCOME_RULES.items() if re.search(pattern, text)],
        "deductions": [name for name, pattern in _DEDUCTION_RULES.items() if re.search(pattern, text)],
        "irs_notice_received": True if _IRS_NOTICE_RE.search(text) else None,
        "deadline_date": deadline,
        "consultation_requested": True if consultation else None,
        "request_summary": _labeled(body, "notes", "notice") or headers.get("subject"),
    }


# --- document checklist -------------------------------------------------------------------

_INCOME_DOCS: dict[str, tuple[str, ...]] = {
    "w2_wages": ("Form W-2 from every employer during the year",),
    "self_employment": (
        "Forms 1099-NEC and 1099-K from clients and payment apps",
        "Business income and expense records, or a profit-and-loss summary",
        "Vehicle mileage log, if a vehicle is used for the work",
        "Records of any estimated tax payments made",
    ),
    "interest_dividends": ("Forms 1099-INT and 1099-DIV",),
    "capital_gains": (
        "Form 1099-B or year-end brokerage statements",
        "Crypto exchange transaction history with cost basis, if any crypto was sold",
    ),
    "rental": (
        "Rent received and expenses for each rental property (repairs, insurance, property tax, fees)",
        "Closing statement for any property bought or sold during the year",
    ),
    "retirement": (
        "Form SSA-1099 for Social Security benefits",
        "Forms 1099-R for pensions and IRA or 401(k) distributions",
    ),
    "unemployment": ("Form 1099-G for unemployment benefits",),
    "other_income": ("Documents for any other income received",),
}
_DEDUCTION_DOCS: dict[str, str] = {
    "mortgage_interest": "Form 1098 mortgage interest statement and property tax bills",
    "charitable": "Receipts or acknowledgment letters for charitable donations",
    "education": "Form 1098-T and receipts for tuition and required course materials",
    "student_loan_interest": "Form 1098-E student loan interest statement",
    "childcare": "Childcare provider's name, address, tax ID, and amounts paid",
    "medical": "Medical and dental expense receipts, including insurance premiums paid",
    "retirement_contributions": "IRA contribution statements (Form 5498) and HSA forms (5498-SA, 1099-SA)",
    "marketplace_insurance": "Form 1095-A from the health insurance marketplace",
}


def _checklist(profile: TaxProfile) -> str:
    joint = profile.filing_status == "married_filing_jointly" or profile.marital_status == "married"
    sections = [
        "## Identity and prior-year return",
        "- [ ] Driver's license or state ID (front and back)" + (" for both spouses" if joint else ""),
        "- [ ] Social Security cards or ITIN letters for everyone on the return (through the secure portal)",
        "- [ ] Prior-year federal and state returns",
        "- [ ] Bank routing and account number for a refund or payment (through the secure portal)",
        "",
        f"## Income documents for {profile.tax_year}",
    ]
    if profile.income_sources:
        sections += [f"- [ ] {doc}" for source in profile.income_sources for doc in _INCOME_DOCS[source]]
    else:
        sections.append("- [ ] Every income document received for the year (W-2s, 1099s)")

    credits = [f"- [ ] {_DEDUCTION_DOCS[item]}" for item in profile.deductions]
    if profile.number_of_dependents:
        credits.append("- [ ] Name, date of birth, and months lived with you for each dependent")
    if credits:
        sections += ["", "## Deductions and credits", *credits]

    if profile.irs_notice_received:
        sections += [
            "",
            "## IRS or state correspondence",
            "- [ ] Every page of each notice or letter received",
            "- [ ] Copies of any response already sent, with proof of mailing",
        ]

    notes = [
        "- Confirm filing status: "
        + (f"the client named {FILING_STATUSES[profile.filing_status]}." if profile.filing_status else "the client did not name one.")
    ]
    if profile.irs_notice_received:
        notes.append("- Check the response deadline printed on the notice before anything else.")
    if "self_employment" in profile.income_sources:
        notes.append("- Confirm whether quarterly estimated payments were made and in what amounts.")
    notes.append("- Ask whether the client lived or worked in more than one state during the year.")
    sections += ["", "## Notes for the reviewing preparer", *notes[:4]]
    return "\n".join(sections)
