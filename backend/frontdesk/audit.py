"""Append-only audit of reads and writes.

Every inbox read of a conversation calls `record_read` in the same transaction as the read,
so a read that is not audited cannot happen: if the audit insert fails, the read rolls back.
"""

from __future__ import annotations

from typing import Any

from .db import Transaction


def record(tx: Transaction, *, actor_type: str, actor_id: str, action: str,
           resource_type: str, resource_id: str, detail: dict[str, Any] | None = None) -> None:
    tx.execute(
        """INSERT INTO app.audit_log (tenant_id, actor_type, actor_id, action, resource_type, resource_id, detail)
           VALUES (app.current_tenant(), :at, :aid, :act, :rt, :rid, :d::jsonb)""",
        {"at": actor_type, "aid": actor_id, "act": action, "rt": resource_type,
         "rid": str(resource_id), "d": detail or {}},
    )


def record_read(tx: Transaction, *, actor_id: str, resource_type: str, resource_id: str,
                actor_type: str = "staff", detail: dict[str, Any] | None = None) -> None:
    record(tx, actor_type=actor_type, actor_id=actor_id, action="read",
           resource_type=resource_type, resource_id=resource_id, detail=detail)


def who_read(tx: Transaction, resource_type: str, resource_id: str) -> list[dict[str, Any]]:
    """Answers 'who read this customer's conversation, and when'."""
    return tx.execute(
        """SELECT actor_type, actor_id, at FROM app.audit_log
           WHERE action = 'read' AND resource_type = :rt AND resource_id = :rid ORDER BY at""",
        {"rt": resource_type, "rid": str(resource_id)},
    )
