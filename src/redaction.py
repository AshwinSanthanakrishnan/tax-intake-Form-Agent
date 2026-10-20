"""Remove numbers a tax client should never send by email, before any text reaches the AI.

Clients often paste a Social Security number or their bank details into an inquiry. The firm
collects those later through a secure channel, so they are masked here, in code, before the email
is used for extraction or drafting. The driver's license number is deliberately left in: it is a
required intake field.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

SSN = "SSN/ITIN"
BANK = "bank account/routing number"

# (kind, pattern). A pattern with a group masks only that group, so the label stays readable.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 123-45-6789 / 123 45 6789: the SSN and ITIN layout (phone numbers are 3-3-4).
    (SSN, re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")),
    # "SSN: 123456789", "social security number 123456789", "ITIN #912345678"
    (
        SSN,
        re.compile(
            r"\b(?:ssn|itin|social security(?: number| no\.?)?|taxpayer id(?:entification)?(?: number)?)"
            r"\s*(?:#|no\.?|number)?\s*[:\-]?\s*(\d{9})\b",
            re.I,
        ),
    ),
    # "account number: 000123456789", "routing # 021000021", "acct 4455667788"
    (
        BANK,
        re.compile(r"\b(?:account|acct|routing|aba)\s*(?:number|no\.?|#)?\s*[:\-]?\s*(\d[\d -]{4,20}\d)\b", re.I),
    ),
)


@dataclass(frozen=True)
class RedactedText:
    text: str
    removed: dict[str, int] = field(default_factory=dict)  # kind -> count

    @property
    def changed(self) -> bool:
        return bool(self.removed)


def redact_sensitive_numbers(text: str) -> RedactedText:
    counts: Counter[str] = Counter()
    for kind, pattern in _PATTERNS:

        def mask(match: re.Match[str], kind: str = kind) -> str:
            counts[kind] += 1
            placeholder = f"[REDACTED {kind}]"
            if match.lastindex:
                start, end = match.span(1)
                whole_start = match.start()
                return match.group(0)[: start - whole_start] + placeholder + match.group(0)[end - whole_start :]
            return placeholder

        text = pattern.sub(mask, text)
    return RedactedText(text=text, removed=dict(counts))


def describe_redactions(removed: dict[str, int]) -> str:
    return ", ".join(f"{kind} ({count})" for kind, count in removed.items())
