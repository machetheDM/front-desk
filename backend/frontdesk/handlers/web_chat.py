"""Website chat: HTTP for simple widgets, WebSocket for progress updates (ADR 0009).

The widget key decides the tenant; the request's Origin must be on that key's allow-list. The
session id comes from the visitor's browser and becomes the channel address, so a visitor's
conversation continues across messages without any login.
"""

from __future__ import annotations

import json
import os
import re

from .. import pipeline, routing, telemetry
from ..llm.base import ModelUnavailable
from .common import body_of, db, deps, header, response

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _route(event: dict, key: str | None):
    return routing.for_widget(key, header(event, "origin"))


def run_turn(route, session: str, text: str, name: str | None, progress=None) -> dict:
    inb = pipeline.Inbound(tenant_id=route.tenant_id, channel="web", channel_address=f"web:{session}", text=text[:4000], display_name=name)
    rec = pipeline.receive(db(), inb)
    if not rec.proceed:
        return {"action": rec.reason, "reply": "Thanks, we have your message." if rec.reason == "rate_limited" else "", "conversation_id": rec.conversation_id}
    if progress:
        progress({"type": "status", "text": "Checking our information..."})
    try:
        o = pipeline.answer(db(), deps(), route.tenant_id, rec)
    except ModelUnavailable as e:
        o = pipeline.degrade(db(), route.tenant_id, rec, str(e))
    except Exception as e:  # anything else still leaves the customer with a person on the way
        o = pipeline.degrade(db(), route.tenant_id, rec, type(e).__name__)
    return {"action": o.action, "reply": o.reply, "conversation_id": rec.conversation_id,
            "citations": [c.quote for c in o.citations]}


def handler(event, context):
    origin = header(event, "origin")
    route = _route(event, header(event, "x-frontdesk-widget-key"))
    if route is None:
        telemetry.metric("WidgetKeyRejected")
        return response(403, {"error": "unknown widget key or origin"})
    b = body_of(event)
    session, text = str(b.get("session", "")), str(b.get("message", "")).strip()
    if not SESSION_RE.match(session) or not text:
        return response(400, {"error": "session (16-64 url-safe chars) and message are required"}, origin)
    return response(200, run_turn(route, session, text, b.get("name")), origin)


def ws_handler(event, context):
    rc = event.get("requestContext", {})
    route_key, conn = rc.get("routeKey"), rc.get("connectionId")
    if route_key == "$connect":
        qs = event.get("queryStringParameters") or {}
        ok = routing.for_widget(qs.get("key"), header(event, "origin")) is not None
        return {"statusCode": 200 if ok else 403}
    if route_key == "$disconnect":
        return {"statusCode": 200}
    import boto3

    api = boto3.client("apigatewaymanagementapi", endpoint_url=os.environ["FRONTDESK_WS_ENDPOINT"])
    send = lambda msg: api.post_to_connection(ConnectionId=conn, Data=json.dumps(msg).encode())
    b = body_of(event)
    route = routing.for_widget(b.get("key"), header(event, "origin"))
    if route is None or not SESSION_RE.match(str(b.get("session", ""))) or not str(b.get("message", "")).strip():
        send({"type": "error", "text": "invalid request"})
        return {"statusCode": 400}
    result = run_turn(route, b["session"], str(b["message"]).strip(), b.get("name"), progress=send)
    # The answer is validated before any of it is sent; it is then delivered in sentence-sized
    # pieces so the widget can render progressively (ADR 0009: no unvalidated tokens).
    for piece in re.split(r"(?<=[.!?])\s+", result["reply"]):
        if piece:
            send({"type": "answer_part", "text": piece})
    send({"type": "done", "action": result["action"], "citations": result.get("citations", [])})
    return {"statusCode": 200}
