"""Order status adapter (the Phase 6 "one real integration" plugs in here).

The agent only ever sees `OrdersAdapter`. The demo tenant uses `ConfigOrdersAdapter`, which
reads the made-up orders from tenant config. A client's system is reached by `HttpOrdersAdapter`
(SEAM(client-order-api): base URL and HMAC secret come from SSM per tenant; nothing calls it
until a client's API is connected in the pilot).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Protocol

ORDER_REF = re.compile(r"\b([A-Z]{2,4}-\d{3,6})\b")


@dataclass(frozen=True)
class OrderStatus:
    ref: str
    status: str
    detail: str


class OrdersAdapter(Protocol):
    def status(self, ref: str, *, requester_address: str | None) -> OrderStatus | None: ...


def _same_number(a: str | None, b: str | None) -> bool:
    digits = lambda s: re.sub(r"\D", "", s or "")[-9:]
    return bool(a and b) and digits(a) == digits(b)


class ConfigOrdersAdapter:
    def __init__(self, orders: dict[str, dict]) -> None:
        self.orders = orders

    def status(self, ref: str, *, requester_address: str | None) -> OrderStatus | None:
        o = self.orders.get(ref.upper())
        # An order reference is guessable; status is only told to the number on the order.
        if o is None or not _same_number(o.get("contact"), requester_address):
            return None
        return OrderStatus(ref.upper(), o["status"], o["detail"])


class HttpOrdersAdapter:
    """Calls a client's order API with an HMAC-signed request (same scheme as our inbound
    webhooks, frontdesk/channels/webhook_signature.py)."""

    def __init__(self, base_url: str, secret: bytes, timeout: float = 3.0) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("client APIs must use HTTPS")
        self.base_url, self.secret, self.timeout = base_url.rstrip("/"), secret, timeout

    def status(self, ref: str, *, requester_address: str | None) -> OrderStatus | None:
        body = json.dumps({"ref": ref, "requester": requester_address}).encode()
        ts = str(int(time.time()))
        sig = hmac.new(self.secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"{self.base_url}/orders/status", data=body, method="POST",
                                     headers={"content-type": "application/json", "x-fd-timestamp": ts, "x-fd-signature": sig})
        # nosemgrep -- see the reason on the next line
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - https enforced in __init__
            data = json.loads(r.read())
        return OrderStatus(ref, data["status"], data.get("detail", "")) if data.get("found") else None
