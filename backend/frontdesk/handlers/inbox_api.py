"""API Gateway (HTTP API, JWT authorizer) -> inbox_api.handle."""

from __future__ import annotations

import json

from .. import inbox_api
from ..channels.whatsapp import WhatsAppSender
from .common import body_of, db


def _sender(tx, channel: str, address: str | None, text: str):
    if channel != "whatsapp" or not address:
        return None  # web replies are picked up by the widget on its next poll
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'whatsapp'")
    if not row:
        return None
    cfg = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
    # SEAM(whatsapp-24h-window): Meta allows free-form replies only within 24 hours of the
    # customer's last message; outside it a pre-approved template is required (docs/PLAN.md Phase 3).
    return WhatsAppSender().send_text(cfg["phone_number_id"], address, text)


def handler(event, context):
    rc = event.get("requestContext", {})
    claims = (rc.get("authorizer") or {}).get("jwt", {}).get("claims", {})
    method = rc.get("http", {}).get("method", "GET")
    return inbox_api.handle(db(), method, event.get("rawPath", ""), claims, body_of(event), _sender,
                            event.get("queryStringParameters") or {})
