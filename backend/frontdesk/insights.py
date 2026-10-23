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
    }


def _p(e: dict) -> dict:
    p = e["payload"]
    return p if isinstance(p, dict) else __import__("json").loads(p)
