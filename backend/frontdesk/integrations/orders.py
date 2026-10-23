"""Order status adapter (the Phase 6 "one real integration" plugs in here).

The agent only ever sees `OrdersAdapter`. The demo tenant uses `ConfigOrdersAdapter`, which
reads the made-up orders from tenant config. A client's system is reached by `HttpOrdersAdapter`,
built by `services.orders_for` when the tenant has an `orders_api` config (base URL in config,
HMAC secret in SSM SecureString). The contract the client implements is
docs/engagement/integration-contract.md, and `integrations/conformance.py` checks their sandbox
against it. SEAM(client-order-api): the remaining step is a real client's API.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from ..channels.webhook_signature import sign

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

    def __init__(self, base_url: str, secret: bytes, timeout: float = 3.0, *, allow_loopback_http: bool = False) -> None:
        u = urllib.parse.urlsplit(base_url)
        loopback = allow_loopback_http and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")
        if u.scheme != "https" and not loopback:
            raise ValueError("client APIs must use HTTPS")
        self.base_url, self.secret, self.timeout = base_url.rstrip("/"), secret, timeout

    def status(self, ref: str, *, requester_address: str | None) -> OrderStatus | None:
        body = json.dumps({"ref": ref, "requester": requester_address}).encode()
        ts, sig = sign(self.secret, body)
        req = urllib.request.Request(f"{self.base_url}/orders/status", data=body, method="POST",
                                     headers={"content-type": "application/json", "x-fd-timestamp": ts, "x-fd-signature": sig})
        # nosemgrep -- see the reason on the next line
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - https enforced in __init__ (loopback http for tests only)
            data = json.loads(r.read())
        return OrderStatus(ref, data["status"], data.get("detail", "")) if data.get("found") else None
