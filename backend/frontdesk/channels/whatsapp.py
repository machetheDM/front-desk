"""WhatsApp through AWS End User Messaging Social (af-south-1).

Inbound: Meta delivers to AWS; AWS publishes an event to our SNS topic; SNS invokes our
Lambda. We verify the SNS signature as well as relying on the topic policy (defence in depth,
DET-08 on failure). Outbound: `SendWhatsAppMessage` with a Meta Cloud API payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class WhatsAppInbound:
    phone_number_id: str        # our business number's id in End User Messaging Social
    from_number: str            # customer's E.164 number
    name: str | None            # the WhatsApp profile name, exactly as given
    text: str
    message_id: str             # Meta's wamid, used to de-duplicate retries
    timestamp: int


def parse_event(sns_message: dict) -> list[WhatsAppInbound]:
    """End User Messaging Social wraps Meta's webhook entry (a JSON string) in
    `whatsAppWebhookEntry`, with the AWS phone-number resource in `context.MetaPhoneNumberIds`.

    SEAM(live-whatsapp-event): the shape follows the AWS event-destination documentation; the
    first live event on the demo number is recorded to tests/fixtures and replaces the
    synthetic one in test_whatsapp.py.
    """
    entry = sns_message.get("whatsAppWebhookEntry")
    if isinstance(entry, str):
        entry = json.loads(entry)
    out = []
    phone_ids = (sns_message.get("context") or {}).get("MetaPhoneNumberIds") or [{}]
    pnid = (phone_ids[0].get("arn") or "").rsplit("/", 1)[-1]
    for change in (entry or {}).get("changes", []):
        value = change.get("value", {})
        names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
        for m in value.get("messages", []):
            if m.get("type") != "text":
                text = f"[{m.get('type', 'unsupported')} message]"
            else:
                text = m["text"]["body"]
            out.append(WhatsAppInbound(
                phone_number_id=pnid or value.get("metadata", {}).get("phone_number_id", ""),
                from_number="+" + m["from"].lstrip("+"), name=names.get(m["from"]), text=text,
                message_id=m["id"], timestamp=int(m.get("timestamp", 0))))
    return out


class WhatsAppSender:
    def __init__(self, client=None, region: str = "af-south-1") -> None:
        if client is None:
            import boto3

            client = boto3.client("socialmessaging", region_name=region)
        self.client = client

    def send_text(self, origination_phone_number_id: str, to: str, text: str) -> str:
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+"),
                   "type": "text", "text": {"preview_url": False, "body": text[:4096]}}
        r = self.client.send_whatsapp_message(originationPhoneNumberId=origination_phone_number_id,
                                              message=json.dumps(payload).encode(), metaApiVersion="v20.0")
        return r.get("messageId", "")
