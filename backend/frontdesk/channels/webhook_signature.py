"""HMAC signatures for webhooks we receive from client systems (and send to them).

Scheme: hex(HMAC-SHA256(secret, timestamp + "." + body)), in `x-fd-signature`, with the Unix
timestamp in `x-fd-timestamp`. Rejected if the timestamp is more than five minutes off (replay
protection) or the signature does not match in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import time

MAX_SKEW_S = 300


class WebhookRejected(ValueError):
    pass


def sign(secret: bytes, body: bytes, ts: int | None = None) -> tuple[str, str]:
    ts = int(ts if ts is not None else time.time())
    return str(ts), hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def verify(secret: bytes, body: bytes, timestamp: str | None, signature: str | None, now: float | None = None) -> None:
    if not timestamp or not signature:
        raise WebhookRejected("missing signature headers")
    try:
        ts = int(timestamp)
    except ValueError as e:
        raise WebhookRejected("bad timestamp") from e
    if abs((now or time.time()) - ts) > MAX_SKEW_S:
        raise WebhookRejected("timestamp outside the allowed window")
    expected = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookRejected("signature mismatch")
