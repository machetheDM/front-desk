"""The owner's numbers, computed from conversation events every time. Nothing here is stored
as truth: if an event is corrected, every figure follows (AGENTS.md: conversations are events)."""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter

from .db import Transaction
from .stats import wilson


def summary(tx: Transaction, since: dt.datetime, until: dt.datetime | None = None) -> dict:
    until = until or dt.datetime.now(dt.UTC)
    ev = tx.execute(
        """SELECT conversation_id::text AS c, type, payload, occurred_at FROM app.conversation_events
           WHERE occurred_at >= :s AND occurred_at < :u ORDER BY event_id""", {"s": since, "u": until})
    received = [e for e in ev if e["type"] == "message_received"]
    after_hours_msgs = {_p(e)["message_id"] for e in received if _p(e).get("after_hours")}
    answered = [e for e in ev if e["type"] == "answer_sent"]
    handed = [e for e in ev if e["type"] in ("handed_over", "degraded")]
    answered_after = [e for e in answered if _p(e).get("in_reply_to") in after_hours_msgs]
    first = [float(_p(e).get("first_reply_seconds", 0)) for e in answered]
    n = len(received)
    hand_rate = wilson(len(handed), n)
    gaps = tx.execute(
        """SELECT m.body_redacted AS q FROM app.conversation_events e
           JOIN app.messages m ON m.tenant_id = e.tenant_id AND m.message_id = (e.payload->>'in_reply_to')::uuid
           WHERE e.type = 'handed_over' AND e.payload->>'reason' = 'unanswerable' AND e.occurred_at >= :s AND e.occurred_at < :u""",
        {"s": since, "u": until})
    return {
        "messages_received": n,
        "answered": len(answered),
        "answered_after_hours": len(answered_after),
        "handed_to_staff": len(handed),
        "handoff_rate": {"rate": round(hand_rate[0], 4), "ci95": [round(hand_rate[1], 4), round(hand_rate[2], 4)]},
        "median_first_reply_seconds": round(statistics.median(first), 2) if first else None,
        "top_intents": Counter(_p(e).get("intent", "general") for e in answered + handed).most_common(8),
        "handoff_reasons": Counter(_p(e).get("reason") for e in handed).most_common(),
        "unanswered_questions": [g["q"] for g in gaps][:20],
        "documents_due_for_review": documents_due(tx, until.date()),
    }


def documents_due(tx: Transaction, today: dt.date, within_days: int = 14) -> list[dict]:
    """Approved documents whose review date has passed or is within two weeks."""
    rows = tx.execute(
        """SELECT title, review_by FROM app.documents
           WHERE approved AND review_by IS NOT NULL AND review_by <= :d ORDER BY review_by, title""",
        {"d": today + dt.timedelta(days=within_days)})
    return [{"title": r["title"], "review_by": str(r["review_by"]), "overdue": str(r["review_by"]) < today.isoformat()} for r in rows]


def _p(e: dict) -> dict:
    p = e["payload"]
    return p if isinstance(p, dict) else __import__("json").loads(p)


def shadow_agreement(tx: Transaction, since: dt.datetime, until: dt.datetime | None = None) -> dict:
    """How often the shadow classifier agreed with what the agent did (docs/MODEL-CARD.md
    section 6). Disagreements where the agent answered but the model wanted a person are the
    cases worth reading: they are where a retrained model could add something."""
    until = until or dt.datetime.now(dt.UTC)
    rows = tx.execute(
        """SELECT type, payload->>'intent' AS intent, payload->'shadow' AS shadow FROM app.conversation_events
           WHERE payload ? 'shadow' AND occurred_at >= :s AND occurred_at < :u""", {"s": since, "u": until})
    scored = [r for r in rows if isinstance(r["shadow"], dict) and "would_hand_over" in r["shadow"]]
    if not scored:
        return {"messages": 0}
    acted = [r["type"] in ("handed_over", "refused", "degraded") for r in scored]
    wanted = [bool(r["shadow"]["would_hand_over"]) for r in scored]
    n = len(scored)
    return {"messages": n,
            "handoff_agreement": round(sum(a == w for a, w in zip(acted, wanted)) / n, 3),
            "intent_agreement": round(sum(r["shadow"].get("intent") == r["intent"] for r in scored) / n, 3),
            "model_wanted_a_person_agent_answered": sum(w and not a for a, w in zip(acted, wanted)),
            "agent_handed_over_model_did_not": sum(a and not w for a, w in zip(acted, wanted))}
