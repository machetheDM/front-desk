"""Query rewriting for Sepedi-English code-switched questions.

Two layers. The glossary (always on, free, deterministic) appends English equivalents for
Sepedi terms so both lexical and vector search can match English documents. The optional
LLM rewrite (Bedrock route only) handles phrasing the glossary misses. Both keep the original
words: rewriting replaces nothing, it only adds, so a wrong glossary entry can lower precision
but cannot erase what the customer asked.
"""

from __future__ import annotations

import functools
import importlib.resources
import re
from dataclasses import dataclass

import yaml

_WORD = re.compile(r"[a-zA-ZÀ-ſ]+(?:-[a-zA-Z]+)?")
_POWER = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kw|kwh|kva|w|v|a)\b", re.I)


@functools.cache
def glossary() -> dict:
    text = importlib.resources.files("frontdesk").joinpath("data/glossary.yaml").read_text()
    data = yaml.safe_load(text)
    return {"phrases": data.get("phrases", {}), "words": data.get("words", {})}


def normalise_units(text: str) -> str:
    # "5 kw", "5KW" and "5kW" must hit the same token in the price list.
    return _POWER.sub(lambda m: f"{m.group(1).replace(',', '.')}{m.group(2).lower()}", text)


@dataclass(frozen=True)
class Rewritten:
    original: str
    expanded: str
    added_terms: tuple[str, ...]
    code_switched: bool


def rewrite(query: str) -> Rewritten:
    g = glossary()
    q = normalise_units(query)
    low = q.lower()
    added: list[str] = []
    for phrase, eng in g["phrases"].items():
        if phrase in low and eng:
            added.append(eng)
    for w in _WORD.findall(low):
        eng = g["words"].get(w)
        if eng:
            added.append(eng)
    code_switched = any(w in g["words"] for w in _WORD.findall(low)) or any(p in low for p in g["phrases"])
    expanded = q if not added else f"{q} ({' '.join(added)})"
    return Rewritten(query, expanded, tuple(added), code_switched)
