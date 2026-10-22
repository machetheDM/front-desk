"""Conversation events: the source of truth for what happened.

`conversations.status` is a convenience for the inbox list and is recomputed from events
here; analytics never read it.
"""

from __future__ import annotations

from typing import Any

from .db import Transaction


def emit(tx: Transaction, conversation_id: str, type_: str, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    if "body" in payload or "text" in payload:
        # The CHECK constraint enforces this too; failing here gives a clearer error.
        raise ValueError("event payloads carry ids and decisions, never message text")
    tx.execute(
        """INSERT INTO app.conversation_events (tenant_id, conversation_id, type, payload)
           VALUES (app.current_tenant(), :c, :t, :p::jsonb)""",
        {"c": str(conversation_id), "t": type_, "p": payload},
    )
    _refresh_status(tx, conversation_id)


def _refresh_status(tx: Transaction, conversation_id: str) -> None:
    rows = tx.execute(
        "SELECT type FROM app.conversation_events WHERE conversation_id = :c ORDER BY event_id",
        {"c": str(conversation_id)},
    )
    status = "open"
    for r in rows:
        t = r["type"]
        if t in ("handed_over", "degraded"):
            status = "handed_over"
        elif t == "resolved":
            status = "resolved"
        elif t == "message_received" and status == "resolved":
            status = "open"
    tx.execute("UPDATE app.conversations SET status = :s WHERE conversation_id = :c",
               {"s": status, "c": str(conversation_id)})


def history(tx: Transaction, conversation_id: str) -> list[dict[str, Any]]:
    return tx.execute(
        "SELECT event_id, type, payload, occurred_at FROM app.conversation_events WHERE conversation_id = :c ORDER BY event_id",
        {"c": str(conversation_id)},
    )
