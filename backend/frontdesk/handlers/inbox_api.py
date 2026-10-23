"""API Gateway (HTTP API, JWT authorizer) -> inbox_api.handle."""

from __future__ import annotations

import json

from .. import inbox_api
from ..channels.whatsapp import WhatsAppSender
from .common import body_of, db


def _sender(tx, channel: str, address: str | None, text: str):
    if channel not in ("whatsapp", "whatsapp-template") or not address:
        return None  # web replies are picked up by the widget on its next poll
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'whatsapp'")
    if not row:
        return None
    cfg = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
    # inbox_api.reply refuses free text outside Meta's 24-hour window; there, staff send the
    # tenant's approved follow-up template ("text" is then the template name).
    if channel == "whatsapp-template":
        return WhatsAppSender().send_template(cfg["phone_number_id"], address, text)
    return WhatsAppSender().send_text(cfg["phone_number_id"], address, text)


def handler(event, context):
    rc = event.get("requestContext", {})
    claims = (rc.get("authorizer") or {}).get("jwt", {}).get("claims", {})
    method = rc.get("http", {}).get("method", "GET")
    return inbox_api.handle(db(), method, event.get("rawPath", ""), claims, body_of(event), _sender,
                            event.get("queryStringParameters") or {})
