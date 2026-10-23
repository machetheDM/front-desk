"""Staff inbox API. Framework-free: `handle(db, request)` -> response dict, wrapped for API
Gateway in handlers/inbox_api.py.

Who may do what comes from the Cognito ID token that API Gateway already verified: the
tenant is the immutable `custom:tenant_id` claim, never a request parameter; the role is the
Cognito group. The staff row must exist and be active in that tenant, checked inside the
tenant's own transaction, so a token for a removed staff member stops working at once.

Every read of a conversation is audited in the same transaction as the read. After the read,
the number of conversations this staff member opened in the last hour is checked; past the
threshold a DET-05 event is raised.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from dataclasses import dataclass

from . import audit, deletion, events, insights, telemetry
from .db import Database, Transaction
from .write_guard import PreconditionFailed

READ_ALERT_THRESHOLD = int(os.environ.get("FRONTDESK_READ_ALERT_THRESHOLD", "40"))
UUID_RE = re.compile(r"^[0-9a-f-]{36}$")


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    sub: str
    email: str
    groups: tuple[str, ...]

    @property
    def is_owner(self) -> bool:
        return "owner" in self.groups


class Denied(Exception):
    status = 403


def principal_from_claims(claims: dict) -> Principal:
    tid = claims.get("custom:tenant_id")
    if not tid:
        raise Denied("token has no tenant")
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):  # API Gateway flattens arrays to "[a b]"
        groups = groups.strip("[]").replace(",", " ").split()
    return Principal(uuid.UUID(tid), claims["sub"], claims.get("email", ""), tuple(groups))


def _staff(tx: Transaction, p: Principal) -> dict:
    row = tx.one("SELECT staff_id::text AS staff_id, display_name, role FROM app.staff WHERE active AND (cognito_sub = :s OR email = :e)",
                 {"s": p.sub, "e": p.email})
    if row is None:
        raise Denied("not an active staff member of this business")
    return row


def _json(status: int, body) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, default=str)}


def handle(db: Database, method: str, path: str, claims: dict, body: dict | None = None, sender=None,
           query: dict | None = None) -> dict:
    try:
        p = principal_from_claims(claims)
        with db.transaction(p.tenant_id) as tx:
            staff = _staff(tx, p)
            actor = f"staff:{staff['staff_id']}"
            if method == "GET" and path == "/v1/inbox/handoffs":
                return _json(200, list_handoffs(tx, actor))
            if m := re.fullmatch(r"/v1/inbox/conversations/([0-9a-f-]{36})", path):
                if method == "GET":
                    return _json(200, read_conversation(tx, actor, m.group(1)))
            if m := re.fullmatch(r"/v1/inbox/conversations/([0-9a-f-]{36})/reply", path):
                return _json(200, reply(tx, staff, m.group(1), (body or {}).get("text", ""), sender))
            if m := re.fullmatch(r"/v1/inbox/conversations/([0-9a-f-]{36})/resolve", path):
                return _json(200, resolve(tx, actor, m.group(1)))
            if path == "/v1/insights/summary":
                if not p.is_owner:
                    raise Denied("owners only")
                days = min(int((query or {}).get("days", 30)), 366)
                audit.record_read(tx, actor_id=actor, resource_type="insights", resource_id=f"last-{days}-days")
                return _json(200, insights.summary(tx, dt.datetime.now(dt.UTC) - dt.timedelta(days=days)))
        if path == "/v1/admin/deletion-requests" and method == "POST":
            if not p.is_owner:
                raise Denied("owners only")
            cid = (body or {}).get("customer_id", "")
            if not UUID_RE.match(cid):
                return _json(400, {"error": "customer_id must be a uuid"})
            from .write_guard import execute

            try:
                result = execute(db, deletion.plan(p.tenant_id, cid, f"staff:{p.sub}"), write=bool((body or {}).get("confirm")), echo=lambda s: None)
            except PreconditionFailed as e:
                return _json(409, {"error": str(e)})
            return _json(200, result or {"dry_run": True, "message": "send confirm: true to delete"})
        return _json(404, {"error": "not found"})
    except Denied as e:
        return _json(403, {"error": str(e)})


def list_handoffs(tx: Transaction, actor: str) -> list[dict]:
    rows = tx.execute(
        """SELECT h.handoff_id::text AS handoff_id, h.conversation_id::text AS conversation_id, h.reason, h.summary,
                  h.created_at, s.display_name AS assigned_to, c.channel
           FROM app.handoffs h JOIN app.staff s ON s.tenant_id = h.tenant_id AND s.staff_id = h.assigned_staff_id
           JOIN app.conversations c ON c.tenant_id = h.tenant_id AND c.conversation_id = h.conversation_id
           WHERE h.resolved_at IS NULL ORDER BY h.created_at""")
    audit.record_read(tx, actor_id=actor, resource_type="handoff_list", resource_id="open", detail={"count": len(rows)})
    return rows


def read_conversation(tx: Transaction, actor: str, conversation_id: str) -> dict:
    conv = tx.one("SELECT conversation_id::text AS id, status, channel, created_at FROM app.conversations WHERE conversation_id = :c",
                  {"c": conversation_id})
    if conv is None:
        # Same response for "not yours" and "does not exist": RLS makes them indistinguishable anyway.
        return {"error": "not found"}
    msgs = tx.execute(
        """SELECT message_id::text AS id, direction, author, body, body_redacted, redactions, created_at
           FROM app.messages WHERE conversation_id = :c ORDER BY created_at""", {"c": conversation_id})
    audit.record_read(tx, actor_id=actor, resource_type="conversation", resource_id=conversation_id, detail={"messages": len(msgs)})
    _check_read_volume(tx, actor)
    return {"conversation": conv, "messages": msgs, "events": events.history(tx, conversation_id)}


def _check_read_volume(tx: Transaction, actor: str) -> None:
    n = tx.scalar(
        """SELECT count(DISTINCT resource_id) FROM app.audit_log WHERE actor_id = :a AND action = 'read'
           AND resource_type = 'conversation' AND at > now() - interval '1 hour'""", {"a": actor})
    if int(n) == READ_ALERT_THRESHOLD + 1:  # once per crossing, not on every read after it
        telemetry.security_event("UnusualTranscriptReads", {"actor": actor, "conversations_last_hour": int(n)})


def reply(tx: Transaction, staff: dict, conversation_id: str, text: str, sender=None) -> dict:
    text = text.strip()
    if not text or len(text) > 4000:
        return {"error": "reply must be 1 to 4000 characters"}
    conv = tx.one("""SELECT v.channel, c.channel_address FROM app.conversations v
                     JOIN app.customers c ON c.tenant_id = v.tenant_id AND c.customer_id = v.customer_id
                     WHERE v.conversation_id = :c""", {"c": conversation_id})
    if conv is None:
        return {"error": "not found"}
    mid = tx.scalar(
        """INSERT INTO app.messages (tenant_id, conversation_id, direction, author, staff_id, body, body_redacted)
           VALUES (app.current_tenant(), :c, 'out', 'staff', :s, :b, :b) RETURNING message_id::text""",
        {"c": conversation_id, "s": staff["staff_id"], "b": text})
    events.emit(tx, conversation_id, "staff_replied", {"message_id": mid, "staff_id": staff["staff_id"]})
    audit.record(tx, actor_type="staff", actor_id=f"staff:{staff['staff_id']}", action="write", resource_type="message", resource_id=mid)
    delivered = sender(tx, conv["channel"], conv["channel_address"], text) if sender else None
    return {"message_id": mid, "delivered": delivered}


def resolve(tx: Transaction, actor: str, conversation_id: str) -> dict:
    events.emit(tx, conversation_id, "resolved", {"by": actor})
    tx.execute("UPDATE app.handoffs SET resolved_at = now() WHERE conversation_id = :c AND resolved_at IS NULL", {"c": conversation_id})
    audit.record(tx, actor_type="staff", actor_id=actor, action="write", resource_type="conversation", resource_id=conversation_id,
                 detail={"resolved": True})
    return {"resolved": conversation_id}
