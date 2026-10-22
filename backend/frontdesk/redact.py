"""PII redaction. Runs on every customer message before any model call, and before any log line.

The model never needs a customer's ID number, card number, bank account, phone number, e-mail
or street address to answer a question about inverter prices, so they are replaced with typed
placeholders. Suburbs are kept on purpose: "installed in Seshego" decides the travel fee.

The redaction record keeps *types and positions*, never the original values, so the record
itself is safe to store next to the redacted text.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

MAX_MODEL_INPUT_CHARS = 2000  # long inputs inflate cost and widen the injection surface


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def is_sa_id(digits: str) -> bool:
    """SA ID: YYMMDD SSSS C A Z. C (citizenship) is 0 or 1, Z is a Luhn check digit."""
    if len(digits) != 13 or digits[10] not in "01":
        return False
    yy, mm, dd = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    try:
        dt.date(2000 + yy if yy < 30 else 1900 + yy, mm, dd)
    except ValueError:
        return False
    return luhn_ok(digits)


# Digit runs that may contain single spaces or dashes ("850101 5800 08 5", "4111-1111-1111-1111").
DIGIT_RUN = re.compile(r"(?<![\d])(?:\d[ -]?){11,18}\d(?![\d])")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\d+])(?:\+?27[ -]?|0)(?:\(?\d{2}\)?[ -]?)\d{3}[ -]?\d{4}(?![\d])")
BANK = re.compile(r"(?i)\b(?:acc(?:oun)?t|acc)\s*(?:no\.?|number|#)?\s*[:=]?\s*(\d[\d -]{5,14}\d)\b")
PASSPORT = re.compile(r"(?i)\bpassport\s*(?:no\.?|number|#)?\s*[:=]?\s*([A-Z]\d{8}|\d{9})\b")
STREET = re.compile(
    r"(?i)\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][a-z]+\s+){1,3}(?:street|st|road|rd|avenue|ave|drive|dr|crescent|cres|lane|ln|close|way)\b\.?"
)


@dataclass
class Redaction:
    kind: str
    start: int
    end: int


@dataclass
class Redacted:
    text: str
    redactions: list[Redaction] = field(default_factory=list)
    truncated: bool = False

    @property
    def kinds(self) -> list[str]:
        return [r.kind for r in self.redactions]

    def as_record(self) -> list[dict]:
        return [{"kind": r.kind, "start": r.start, "end": r.end} for r in self.redactions]


def _classify_digits(raw: str) -> str | None:
    digits = re.sub(r"[ -]", "", raw)
    if is_sa_id(digits):
        return "ID_NUMBER"
    if 13 <= len(digits) <= 19 and luhn_ok(digits):
        return "CARD_NUMBER"
    return None


def redact(text: str, *, truncate: bool = True) -> Redacted:
    spans: list[tuple[int, int, str]] = []

    def add(start: int, end: int, kind: str) -> None:
        if any(not (end <= s or start >= e) for s, e, _ in spans):
            return  # first (more specific) match wins
        spans.append((start, end, kind))

    for m in DIGIT_RUN.finditer(text):
        kind = _classify_digits(m.group(0))
        if kind:
            add(m.start(), m.end(), kind)
    for m in BANK.finditer(text):
        add(m.start(1), m.end(1), "BANK_ACCOUNT")
    for m in PASSPORT.finditer(text):
        add(m.start(1), m.end(1), "PASSPORT")
    for m in EMAIL.finditer(text):
        add(m.start(), m.end(), "EMAIL")
    for m in PHONE.finditer(text):
        add(m.start(), m.end(), "PHONE")
    for m in STREET.finditer(text):
        add(m.start(), m.end(), "STREET_ADDRESS")

    spans.sort()
    out, pos, records = [], 0, []
    for s, e, kind in spans:
        out.append(text[pos:s])
        placeholder = f"[{kind}]"
        start = sum(len(p) for p in out)
        out.append(placeholder)
        records.append(Redaction(kind, start, start + len(placeholder)))
        pos = e
    out.append(text[pos:])
    red = "".join(out)
    truncated = False
    if truncate and len(red) > MAX_MODEL_INPUT_CHARS:
        red, truncated = red[:MAX_MODEL_INPUT_CHARS], True
    return Redacted(red, records, truncated)


# Patterns used by the eval metric `pii_reaching_model`: anything here in a model input is a failure.
def pii_leaks(text: str) -> list[str]:
    found = []
    for m in DIGIT_RUN.finditer(text):
        if _classify_digits(m.group(0)):
            found.append(_classify_digits(m.group(0)) or "")
    if EMAIL.search(text):
        found.append("EMAIL")
    if PHONE.search(text):
        found.append("PHONE")
    return found
