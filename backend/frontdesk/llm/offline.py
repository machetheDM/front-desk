"""The offline language model (ADR 0006): extractive, deterministic, free.

It answers by quoting approved chunks and hands over when no chunk covers what was asked. It
never writes text that is not in a source, so it cannot invent a price. It is the floor every
pull request is measured against, not the product.

How "covers what was asked" is decided:

1. The question becomes *term groups*. An English word is a group of one (stemmed). A Sepedi
   or Afrikaans word from the glossary is replaced by the group of its English equivalents, so
   "theko" is satisfied by "price" or "cost". Generic words ("how", "much") and context words
   ("my house", "selling") are dropped: they say why someone asks, not what they ask about.
2. Each of the top retrieved chunks is scored by the share of groups it satisfies (with typo
   tolerance for long words, and exact matching for numbers and power ratings).
3. The best chunk wins, ties broken by reranker order. A second chunk is joined only when the
   question compares things, or names a place or a power rating the first chunk lacks: "5kW
   backup installed in Seshego" needs the price row and the travel-fee row.
4. Below the coverage threshold, or with a place or rating no chunk mentions, it hands over.

The threshold is tuned on the dev split only; docs/MODEL-CARD.md reports the clean holdout.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass

from ..retrieval.query_rewrite import glossary, normalise_units
from ..retrieval.search import Hit
from .base import DraftRequest, DraftResult, Usage
from .schemas import AnswerDraft, Citation

COVERAGE_THRESHOLD = 0.7  # tuned on the dev split; see docs/MODEL-CARD.md
TOP_N = 5

GENERIC = set("""
a an and are as at be but by can could do does did for from get got have has how i if in into is it its
just me my of on or our please plz so that the their them there these they this to u us was we what whats
when where which who why will with would you your yours hi hello there much many long cost costs price prices
charge charges much big small need want know tell about am im any some here also too very really guys da 4 hw
ke le ya go ka ko e sa na ga tla ba di mo nka julle die op wat is n een tse
""".split())
# Words that explain the customer's situation rather than name what they want to know.
CONTEXT = set("""
house home property place selling sell buy buying get getting need problem question questions info information
today tomorrow week now someone something anything thing things people number phone email id details dear
first look come see okay yes no work day talk speak myself still bought
""".split())
UNIT_TOKEN = re.compile(r"^\d+(?:\.\d+)?(kw|kwh|kva|w)$")
WORD = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
PLACEHOLDER = re.compile(r"\[[A-Z_]+\]")
COMPARATIVE = re.compile(r"\b(difference|compare|between|versus|vs\.?|and the)\b", re.I)
LOCATION_PREP = re.compile(r"\b(?:in|at|to|ko|near|around|from|into)\s+((?:[A-Z][a-z]+)(?:\s[A-Z][a-z]+)?)")


def stem(w: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


# Answer-type groups: a price question is only covered by a chunk that states a price, a
# "when" question by one that states a time, a "how long" question by one with a duration.
ANSWER_TYPE_CUES = [
    ("<money>", re.compile(r"\b(how much|price|cost|charge|fee|bokae|theko|hoeveel|kos)\b", re.I), re.compile(r"\bR\s?\d")),
    ("<time>", re.compile(r"\b(what time|opening|open|close|bula|oop|hours)\b", re.I), re.compile(r"\b\d{1,2}:\d{2}\b|\bclosed\b", re.I)),
    ("<duration>", re.compile(r"\b(how long|how many (hours|days|years|weeks)|how fast|tse kae)\b", re.I),
     re.compile(r"\b(\d+|one|two|three)(\s?(to|-)\s?\d+)?[\s-]?(years?|days?|hours?|weeks?|working days)\b|\bone to two\b", re.I)),
]


def answer_type_groups(question: str) -> list[frozenset[str]]:
    return [frozenset({tag}) for tag, cue, _ in ANSWER_TYPE_CUES if cue.search(question)]


def term_groups(question: str) -> list[frozenset[str]]:
    g = glossary()
    text = PLACEHOLDER.sub(" ", normalise_units(question)).lower()
    groups: list[frozenset[str]] = []
    for phrase, eng in g["phrases"].items():
        if phrase in text:
            text = text.replace(phrase, " ")
            alts = frozenset(stem(w) for w in WORD.findall(eng.lower()) if w not in GENERIC)
            if alts:
                groups.append(alts)
    for w in WORD.findall(text):
        if w in GENERIC or w in CONTEXT or len(w) < 2:
            continue
        if w in g["words"]:
            alts = frozenset(stem(x) for x in WORD.findall(g["words"][w].lower()) if x not in GENERIC)
            if alts:
                groups.append(alts)
            continue
        groups.append(frozenset({stem(w)}))
    # Duplicates would double-count one idea ("battery ... batteries").
    return list(dict.fromkeys(groups + answer_type_groups(question)))


def _present(term: str, vocab: set[str]) -> bool:
    if term in vocab:
        return True
    if term.startswith("<"):
        return False
    if UNIT_TOKEN.match(term) or any(c.isdigit() for c in term):
        return False  # numbers and ratings must match exactly: 5kw is not 8kw
    if len(term) >= 5:
        if any(v.startswith(term) or (term.startswith(v) and len(v) >= 5) for v in vocab):
            return True
        return bool(difflib.get_close_matches(term, list(vocab), n=1, cutoff=0.8))
    return False


def vocab_of(text: str) -> set[str]:
    return {stem(w) for w in WORD.findall(normalise_units(text).lower())}


def coverage(groups: list[frozenset[str]], text: str) -> float:
    if not groups:
        return 0.0
    v = vocab_of(text) | {tag for tag, _, want in ANSWER_TYPE_CUES if want.search(text)}
    return sum(any(_present(t, v) for t in g) for g in groups) / len(groups)


def places_in(question: str) -> list[str]:
    return [m.group(1) for m in LOCATION_PREP.finditer(question)]


def units_in(text: str) -> set[str]:
    return {w for w in WORD.findall(normalise_units(text).lower()) if UNIT_TOKEN.match(w)}


def missing_entities(question: str, chunk_text: str, known: list[str]) -> list[str]:
    """Places and power ratings the question names that the chosen chunks and the tenant's own
    configuration do not know. "Install in Musina?" retrieves the travel-fee chunk with high
    word overlap; Musina is not in it, so the honest answer is a handoff."""
    known_l = " ".join(known).lower()
    low = chunk_text.lower()
    missing = [p for p in places_in(question) if p.lower() not in low and p.lower() not in known_l]
    return missing + sorted(units_in(question) - units_in(chunk_text))


@dataclass
class Choice:
    chunks: list[Hit]
    coverage: float


def _text(cs: list[Hit]) -> str:
    return " ".join(f"{c.heading} {c.text}" for c in cs)


def choose(question: str, hits: list[Hit]) -> Choice | None:
    groups = term_groups(question)
    if not groups or not hits:
        return None
    cands = hits[:TOP_N]
    scored = sorted(((coverage(groups, _text([h])), -i, h) for i, h in enumerate(cands)), key=lambda x: (x[0], x[1]), reverse=True)
    best_cov, _, best = scored[0]
    chosen = [best]
    if COMPARATIVE.search(question) or missing_entities(question, _text(chosen), []):
        for _, _, h in scored[1:]:
            joint = coverage(groups, _text(chosen + [h]))
            gap_before = len(missing_entities(question, _text(chosen), []))
            gap_after = len(missing_entities(question, _text(chosen + [h]), []))
            if joint > best_cov or gap_after < gap_before:
                chosen.append(h)
                best_cov = max(best_cov, joint)
                break
    return Choice(chosen, best_cov)


class ExtractiveAnswerer:
    name = "offline-extractive-v1"

    def __init__(self, threshold: float = COVERAGE_THRESHOLD) -> None:
        self.threshold = threshold

    def draft(self, req: DraftRequest, *, large: bool = False, run_tool=None) -> DraftResult:
        t0 = time.perf_counter()
        # What the "model" was given from the customer: the redacted question. Sources are
        # the company's own approved text and are tracked separately (Outcome.retrieved).
        inputs = [req.question]
        ok_tools = [t for t in req.tool_results if t.get("ok")]
        if ok_tools:
            d = AnswerDraft(action="answer", answer=" ".join(str(t["result"]) for t in ok_tools))
            return DraftResult(d, self.name, Usage(calls=1), (time.perf_counter() - t0) * 1000, inputs)
        c = choose(req.question, req.sources)
        if c is None or c.coverage < self.threshold or missing_entities(req.question, _text(c.chunks), req.known_entities):
            d = AnswerDraft(action="handoff", handoff_reason="unanswerable")
            return DraftResult(d, self.name, Usage(calls=1), (time.perf_counter() - t0) * 1000, inputs)
        d = AnswerDraft(action="answer", answer=" ".join(h.text for h in c.chunks),
                        citations=[Citation(chunk_id=h.chunk_id, quote=h.text[:200]) for h in c.chunks])
        return DraftResult(d, self.name, Usage(calls=1), (time.perf_counter() - t0) * 1000, inputs)

    def summarise(self, lines: list[str]) -> tuple[str, Usage]:
        return "\n".join(lines[:3]), Usage()
