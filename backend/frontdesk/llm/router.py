"""Model routing: the small model first, the large one only when the question needs it.

Haiku handles most WhatsApp questions (one product, one price) at about a twentieth of
Sonnet's price per token (docs/evidence/pricing-snapshot.json). The large model is used for
multi-part or comparative questions, long messages, and as the retry when the small model's
draft fails validation. The route is recorded on every answer so cost and latency per route
are measured, not assumed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..retrieval.search import Hit

COMPARE = re.compile(r"\b(difference|compare|versus|vs\.?|better|which (one|is)|or the)\b", re.I)


@dataclass(frozen=True)
class Route:
    tier: str  # "small" | "large"
    reason: str


def choose(question: str, hits: list[Hit], *, attempt: int = 0) -> Route:
    forced = os.environ.get("FRONTDESK_FORCE_ROUTE")
    if forced in ("small", "large"):
        return Route(forced, "forced by FRONTDESK_FORCE_ROUTE")
    if attempt > 0:
        return Route("large", "retry after validation failure")
    if len(question) > 280:
        return Route("large", "long message")
    if question.count("?") >= 2 or COMPARE.search(question):
        return Route("large", "multi-part or comparison")
    if hits and hits[0].extra.get("code_switched") and (hits[0].rerank or 0) < 1.0:
        return Route("large", "code-switched with weak retrieval")
    return Route("small", "default")
