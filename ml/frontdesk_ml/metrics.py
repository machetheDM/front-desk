"""Evaluation metrics. Definitions match docs/PLAN.md section 5 exactly."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s ,]+", "", s)


def gold_match(hit: dict, gold) -> bool:
    """hit: {doc_slug, heading, text}."""
    if hit["doc_slug"] != gold.doc or hit["heading"].strip().lower() != gold.heading.strip().lower():
        return False
    return gold.contains is None or gold.contains.lower() in (hit["heading"] + " " + hit["text"]).lower()


def first_gold_rank(hits: list[dict], golds) -> int | None:
    for rank, h in enumerate(hits, 1):
        if any(gold_match(h, g) for g in golds):
            return rank
    return None


def recall_at_k(ranks: Iterable[int | None], k: int) -> float:
    ranks = list(ranks)
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks) if ranks else 0.0


def mrr(ranks: Iterable[int | None], cutoff: int = 10) -> float:
    ranks = list(ranks)
    return sum(1 / r for r in ranks if r is not None and r <= cutoff) / len(ranks) if ranks else 0.0


def facts_ok(reply: str, facts: list[str], forbidden: list[str]) -> bool:
    r = norm(reply)
    return all(norm(f) in r for f in facts) and not any(norm(f) in r for f in forbidden)


def decided_to_answer(action: str) -> bool:
    return action == "answer"


def rate(xs: list[bool]) -> float:
    return sum(xs) / len(xs) if xs else 0.0
