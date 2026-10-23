"""POPIA deletion of a customer: reaches messages, the customer record, and any knowledge chunks
(with their embeddings) that were made from that customer's conversations.

What remains, on purpose: conversation events and the audit log. They hold ids, decisions and
timings, never message text (enforced by a CHECK constraint and a test), so the business keeps
its statistics and the audit trail survives, without personal information. The deletion itself
is audited and reported back to the requester.
"""

from __future__ import annotations

from . import audit
from .db import Transaction
from .write_guard import WritePlan


def erase_customer(tx: Transaction, customer_id: str, requested_by: str) -> dict:
    """The erasure itself, inside the caller's transaction (used by `plan` and by offboarding)."""
    req = tx.scalar("INSERT INTO app.deletion_requests (tenant_id, customer_id, requested_by) VALUES (app.current_tenant(), :c, :by) RETURNING request_id::text",
                    {"c": customer_id, "by": requested_by})
    msgs = tx.execute(
        """UPDATE app.messages SET body = '[deleted on request]', body_redacted = '[deleted on request]', redactions = '[]'
           WHERE conversation_id IN (SELECT conversation_id FROM app.conversations WHERE customer_id = :c)
           RETURNING message_id""", {"c": customer_id})
    chunks = tx.execute("DELETE FROM app.chunks WHERE source_customer_id = :c RETURNING chunk_id", {"c": customer_id})
    tx.execute("UPDATE app.handoffs SET summary = '[deleted on request]' WHERE conversation_id IN (SELECT conversation_id FROM app.conversations WHERE customer_id = :c)",
               {"c": customer_id})
    tx.execute("UPDATE app.customers SET channel_address = NULL, display_name = NULL, deleted_at = now() WHERE customer_id = :c",
               {"c": customer_id})
    report = {"messages_erased": len(msgs), "chunks_and_embeddings_deleted": len(chunks), "customer_record": "anonymised"}
    tx.execute("UPDATE app.deletion_requests SET completed_at = now(), report = :r::jsonb WHERE request_id = :q", {"r": report, "q": req})
    audit.record(tx, actor_type="staff", actor_id=requested_by, action="delete", resource_type="customer",
                 resource_id=customer_id, detail=report)
    return {"request_id": req, **report}


def plan(tenant_id, customer_id: str, requested_by: str) -> WritePlan:
    def precondition(tx: Transaction) -> str | None:
        row = tx.one("SELECT deleted_at FROM app.customers WHERE customer_id = :c", {"c": customer_id})
        if row is None:
            return "no such customer in this tenant"
        if row["deleted_at"] is not None:
            return "customer already deleted"
        return None

    def apply(tx: Transaction) -> dict:
        return erase_customer(tx, customer_id, requested_by)

    return WritePlan(f"POPIA deletion of customer {customer_id}", tenant_id, ["messages", "knowledge chunks from conversations", "customer record"], precondition, apply)
