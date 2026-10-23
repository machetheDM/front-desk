"""From an inbound message to a persisted outcome: the part of the system that writes.

Three steps, each in its own transaction, matching the Step Functions states:

  receive(): customer, conversation, inbound message (raw for staff, redacted for the model),
             consent, rate limit, `message_received`. Returns ids only.
  answer():  runs the agent on the stored message and persists the outcome: outbound message,
             events, handoff with a named staff member, model usage.
  degrade(): the language layer failed: hand over and tell the customer a person will reply.

Step Functions payloads carry ids, never message text, so customer words are not copied into
execution history. The web chat runs the same three steps in one Lambda.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from . import consent, events, ratelimit, telemetry
from .agent import policy
from .agent.state_machine import Agent, Deps, Outcome, Turn, is_office_hours, office_hours, pick_staff
from .db import Database, Transaction
from .llm import metering
from .redact import redact


@dataclass
class Inbound:
    tenant_id: uuid.UUID
    channel: str
    channel_address: str | None
    text: str
    display_name: str | None = None
    channel_message_id: str | None = None
    received_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


@dataclass
class Received:
    conversation_id: str
    message_id: str
    customer_id: str
    first_message: bool
    proceed: bool
    reason: str = ""
    duplicate: bool = False


def _customer(tx: Transaction, inb: Inbound) -> tuple[str, bool]:
    if inb.channel_address:
        row = tx.one("SELECT customer_id::text AS id FROM app.customers WHERE channel = :c AND channel_address = :a AND deleted_at IS NULL",
                     {"c": inb.channel, "a": inb.channel_address})
        if row:
            return row["id"], False
    cid = tx.scalar(
        """INSERT INTO app.customers (tenant_id, channel, channel_address, display_name)
           VALUES (app.current_tenant(), :c, :a, :n) RETURNING customer_id::text""",
        {"c": inb.channel, "a": inb.channel_address, "n": inb.display_name},
    )
    return cid, True


def _conversation(tx: Transaction, customer_id: str, channel: str) -> str:
    row = tx.one(
        """SELECT conversation_id::text AS id FROM app.conversations WHERE customer_id = :c AND status <> 'resolved'
           ORDER BY created_at DESC LIMIT 1""", {"c": customer_id})
    if row:
        return row["id"]
    return tx.scalar("INSERT INTO app.conversations (tenant_id, customer_id, channel) VALUES (app.current_tenant(), :c, :ch) RETURNING conversation_id::text",
                     {"c": customer_id, "ch": channel})


def receive(db: Database, inb: Inbound) -> Received:
    with db.transaction(inb.tenant_id) as tx:
        if inb.channel_message_id:
            dup = tx.one("SELECT message_id::text AS m, conversation_id::text AS c FROM app.messages WHERE channel_message_id = :x",
                         {"x": inb.channel_message_id})
            if dup:  # WhatsApp retries deliveries; a retry must not produce a second answer
                return Received(dup["c"], dup["m"], "", False, False, "duplicate", True)
        customer_id, first = _customer(tx, inb)
        conv = _conversation(tx, customer_id, inb.channel)
        red = redact(inb.text)
        mid = tx.scalar(
            """INSERT INTO app.messages (tenant_id, conversation_id, direction, author, body, body_redacted, redactions, channel_message_id, created_at)
               VALUES (app.current_tenant(), :c, 'in', 'customer', :b, :br, :r::jsonb, :x, :t) RETURNING message_id::text""",
            {"c": conv, "b": inb.text, "br": red.text, "r": red.as_record(), "x": inb.channel_message_id, "t": inb.received_at},
        )
        events.emit(tx, conv, "message_received", {"message_id": mid, "channel": inb.channel, "redactions": red.kinds,
                                                   "after_hours": _after_hours(tx, inb.received_at)})
        if first:
            consent.record(tx, customer_id, "service", True, inb.channel, "customer-initiated conversation")
        if policy.is_opt_out(red.text):
            consent.opt_out(tx, customer_id, inb.channel)
            return Received(conv, mid, customer_id, first, True, "opt_out")
        if not consent.may_answer(tx, customer_id):
            return Received(conv, mid, customer_id, first, False, "opted_out_earlier")
        rl = ratelimit.check_and_count(tx, str(inb.tenant_id), inb.channel_address)
        if not rl.allowed:
            events.emit(tx, conv, "rate_limited", {"message_id": mid, "scope": rl.scope})
            telemetry.metric("RateLimited")
            return Received(conv, mid, customer_id, first, False, "rate_limited")
        return Received(conv, mid, customer_id, first, True)


def _after_hours(tx: Transaction, t: dt.datetime) -> bool:
    tz = (tx.one("SELECT timezone FROM app.tenants") or {}).get("timezone") or "Africa/Johannesburg"
    return not is_office_hours(t.astimezone(ZoneInfo(tz)), office_hours(tx))


def _history(tx: Transaction, conversation_id: str, before_message: str) -> list[dict]:
    rows = tx.execute(
        """SELECT author, coalesce(body_redacted, body) AS text FROM app.messages
           WHERE conversation_id = :c AND message_id <> :m ORDER BY created_at DESC LIMIT 6""",
        {"c": conversation_id, "m": before_message})
    out = []
    for r in reversed(rows):
        # Staff and customer text going back to the model is re-redacted: staff may paste details.
        text = redact(r["text"]).text if r["author"] != "customer" else r["text"]
        out.append({"role": "customer" if r["author"] == "customer" else "assistant", "text": text})
    return out


def answer(db: Database, deps: Deps, tenant_id: uuid.UUID, rec: Received) -> Outcome:
    with db.transaction(tenant_id) as tx:
        m = tx.one("""SELECT m.body, m.created_at, c.channel_address, c.display_name, v.channel FROM app.messages m
                      JOIN app.conversations v ON v.tenant_id = m.tenant_id AND v.conversation_id = m.conversation_id
                      JOIN app.customers c ON c.tenant_id = v.tenant_id AND c.customer_id = v.customer_id
                      WHERE m.message_id = :m""", {"m": rec.message_id})
        turn = Turn(text=m["body"], channel=m["channel"], channel_address=m["channel_address"], customer_name=m["display_name"],
                    first_message=rec.first_message, received_at=_as_dt(m["created_at"]),
                    history=_history(tx, rec.conversation_id, rec.message_id))
        o = Agent(deps).run(tx, turn)
        persist(tx, rec, o)
        return o


def persist(tx: Transaction, rec: Received, o: Outcome) -> str:
    author = "assistant" if o.action in ("answer", "handoff", "refused", "degraded") else "system"
    out_id = tx.scalar(
        """INSERT INTO app.messages (tenant_id, conversation_id, direction, author, body, body_redacted)
           VALUES (app.current_tenant(), :c, 'out', :a, :b, :b) RETURNING message_id::text""",
        {"c": rec.conversation_id, "a": author, "b": o.reply},
    )
    base = {"message_id": out_id, "in_reply_to": rec.message_id, "intent": o.intent, "trace": o.trace,
            "latency_ms": round(o.latency_ms, 1), "route": o.route, "model": o.model_id, "prompt": o.prompt_version}
    if o.action == "answer":
        events.emit(tx, rec.conversation_id, "answer_sent", {**base, "citations": [c.chunk_id for c in o.citations],
                                                             "first_reply_seconds": round(o.latency_ms / 1000, 3)})
    elif o.action in ("handoff", "degraded"):
        staff = o.assigned_staff or pick_staff(tx, o.handoff_reason or "", o.intent)
        if staff:
            tx.execute(
                """INSERT INTO app.handoffs (tenant_id, conversation_id, reason, summary, assigned_staff_id)
                   VALUES (app.current_tenant(), :c, :r, :s, :st)""",
                {"c": rec.conversation_id, "r": o.handoff_reason, "s": o.summary or o.reply, "st": staff["staff_id"]})
            tx.execute("UPDATE app.conversations SET assigned_staff_id = :s WHERE conversation_id = :c",
                       {"s": staff["staff_id"], "c": rec.conversation_id})
        events.emit(tx, rec.conversation_id, "degraded" if o.action == "degraded" else "handed_over",
                    {**base, "reason": o.handoff_reason, "staff_id": staff["staff_id"] if staff else None,
                     "requests": [r["kind"] for r in o.requests]})
        if o.action == "degraded":
            telemetry.metric("Degraded")
    elif o.action == "refused":
        events.emit(tx, rec.conversation_id, "refused", {**base, "reason": o.handoff_reason})
    elif o.action == "opted_out":
        events.emit(tx, rec.conversation_id, "opted_out", base)
    if o.guardrail_intervened:
        telemetry.metric("GuardrailIntervened")
    if "canary_leak" in " ".join(o.validation_problems):
        telemetry.security_event("CanaryTokenLeaked", {"conversation_id": rec.conversation_id})
    if o.model_id and o.usage.calls:
        cost = metering.record(tx, o.model_id, o.usage)
        telemetry.metric("ModelCostUsd", cost, "None", Route=o.route)
    telemetry.metric("AnswerLatencyMs", o.latency_ms, "Milliseconds", Route=o.route)
    return out_id


def degrade(db: Database, tenant_id: uuid.UUID, rec: Received, error: str = "") -> Outcome:
    """Called by the Step Functions Catch: the answer step failed outright (timeout, crash)."""
    with db.transaction(tenant_id) as tx:
        tenant = tx.one("SELECT name FROM app.tenants") or {"name": "us"}
        staff = pick_staff(tx, "model_unavailable", "general")
        o = Outcome(action="degraded", reply=f"Thanks for your message. A person from {tenant['name']} will reply to you here as soon as possible.",
                    intent="general", redacted=redact(""), handoff_reason="model_unavailable", assigned_staff=staff,
                    summary=f"The assistant was unavailable ({error[:80]}). Please read the customer's last message and reply.",
                    trace=["received", "degraded"])
        persist(tx, rec, o)
        return o


def _as_dt(v) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.UTC)
    return dt.datetime.fromisoformat(str(v).replace(" ", "T")).replace(tzinfo=dt.UTC)
