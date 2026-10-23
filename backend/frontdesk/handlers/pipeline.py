"""Step Functions tasks for WhatsApp: Screen -> Answer -> Deliver, Catch -> Degrade.
Payloads carry ids only. The text a task needs is read from the database inside the task."""

from __future__ import annotations

from .. import pipeline
from ..channels.whatsapp import WhatsAppSender
from .common import as_uuid, db, deps


def _rec(e: dict) -> pipeline.Received:
    return pipeline.Received(e["conversation_id"], e["message_id"], e.get("customer_id", ""), bool(e.get("first_message")), bool(e.get("proceed", True)))


def screen(event, context):
    # Consent can change between intake and now (a STOP in a parallel message): check again.
    from .. import consent

    with db().transaction(as_uuid(event["tenant_id"])) as tx:
        ok = consent.may_answer(tx, event["customer_id"]) if event.get("customer_id") else True
    return {**event, "proceed": bool(event.get("proceed", True)) and ok}


def answer(event, context):
    o = pipeline.answer(db(), deps(), as_uuid(event["tenant_id"]), _rec(event))
    return {**event, "action": o.action}


def _send_latest_reply(event):
    with db().transaction(as_uuid(event["tenant_id"])) as tx:
        row = tx.one("""SELECT m.body, c.channel_address FROM app.messages m
                        JOIN app.conversations v ON v.tenant_id = m.tenant_id AND v.conversation_id = m.conversation_id
                        JOIN app.customers c ON c.tenant_id = v.tenant_id AND c.customer_id = v.customer_id
                        WHERE m.conversation_id = :c AND m.direction = 'out' ORDER BY m.created_at DESC LIMIT 1""",
                     {"c": event["conversation_id"]})
    if row and event.get("phone_number_id"):
        return WhatsAppSender().send_text(event["phone_number_id"], row["channel_address"], row["body"])
    return None


def deliver(event, context):
    return {**event, "sent": _send_latest_reply(event)}


def degrade(event, context):
    err = (event.get("error") or {}).get("Error", "unknown")
    pipeline.degrade(db(), as_uuid(event["tenant_id"]), _rec(event), err)
    return {**event, "action": "degraded", "sent": _send_latest_reply(event)}
