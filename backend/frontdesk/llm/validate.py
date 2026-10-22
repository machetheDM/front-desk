"""Output validation: an answer is sent only if it passes every check here.

This is the last line between the model and the customer. It enforces refusals 2, 3 and 5 in
code rather than trusting the prompt: no source no answer, no commitment, no guessed title,
and every number the customer reads must be written in a cited source.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..retrieval.search import Hit
from .schemas import AnswerDraft

MONEY = re.compile(r"R\s?\d[\d\s ,]*(?:\.\d+)?")
PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
UNITS = re.compile(r"\d+(?:\.\d+)?\s?(?:kWh|kW|kVA|W)\b", re.I)
DURATIONS = re.compile(r"\b\d+(?:\s?(?:to|-)\s?\d+)?[\s-]?(?:years?|yrs?|days?|hours?|weeks?|months?)\b", re.I)
TIMES = re.compile(r"\b\d{1,2}:\d{2}\b")
URL = re.compile(r"https?://\S+|www\.\S+", re.I)
TITLES = re.compile(r"\b(Mr|Mrs|Ms|Miss|Sir|Madam|Ma'am|Mam)\b\.?")
COMMITMENT = re.compile(
    r"(?i)\b(?:i|we)(?:'ll| will| have| can| am going to)?\s+(?:give|offer|approve|refund|book|reserve|confirm|guarantee|waive|match)\b"
    r"|\byou(?:'re| are) (?:booked|confirmed|approved|getting)\b|\bbooked (?:you|for)\b|\bdiscount of\b|\bfor free\b"
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s ,]+", "", s)


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)


def facts_in(text: str) -> list[str]:
    out = []
    for rx in (MONEY, PERCENT, UNITS, DURATIONS, TIMES):
        out += [m.group(0).strip() for m in rx.finditer(text)]
    return [f for f in out if re.search(r"\d", f)]


def validate(draft: AnswerDraft, sources: list[Hit], *, tool_results: list[dict] | None = None,
             canary: str = "", small_talk: bool = False) -> Verdict:
    problems: list[str] = []
    tool_text = " ".join(str(t.get("result", "")) for t in (tool_results or []))
    by_id = {h.chunk_id: h for h in sources}
    if canary and canary.lower() in draft.answer.lower():
        problems.append("canary_leak")
    if TITLES.search(draft.answer):
        problems.append("title_or_pronoun_guess")
    if draft.commitments:
        problems.append("commitments_not_allowed")
    if draft.action == "handoff":
        return Verdict(not problems, problems)
    if not draft.answer.strip():
        problems.append("empty_answer")
    cited = []
    for c in draft.citations:
        h = by_id.get(c.chunk_id)
        if h is None:
            problems.append(f"citation_not_in_sources:{c.chunk_id}")
            continue
        if norm(c.quote) not in norm(f"{h.heading} {h.text}"):
            problems.append(f"quote_not_in_source:{c.chunk_id}")
        cited.append(f"{h.heading} {h.text}")
    if not small_talk and not cited and not tool_text:
        problems.append("no_source_no_answer")
    support = norm(" ".join(cited) + " " + tool_text)
    for f in facts_in(draft.answer):
        if norm(f) not in support:
            problems.append(f"ungrounded_fact:{f}")
    # No links at all: a link is the payload of most indirect-injection and phishing attacks,
    # and nothing a customer needs from us requires one.
    if URL.search(draft.answer):
        problems.append("link_not_allowed")
    for m in COMMITMENT.finditer(draft.answer):
        if norm(m.group(0)) not in support:
            problems.append(f"commitment_language:{m.group(0)}")
    return Verdict(not problems, problems)
