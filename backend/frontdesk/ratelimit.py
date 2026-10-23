"""Per-sender and per-tenant rate limits (cost denial of service, spam). Fixed windows in
Postgres under RLS, so one tenant's traffic can never exhaust another tenant's budget.
Sender keys are hashed: the limits table never holds a phone number."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from .db import Transaction

LIMITS = [
    # (scope, window seconds, max hits)
    ("sender", 60, 8),
    ("sender", 3600, 60),
    ("tenant", 3600, 600),
]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    scope: str = ""
    retry_after_s: int = 0


def sender_key(tenant_id: str, address: str) -> str:
    return "sender:" + hashlib.sha256(f"{tenant_id}:{address}".encode()).hexdigest()[:24]


def check_and_count(tx: Transaction, tenant_id: str, address: str | None, now: dt.datetime | None = None) -> Decision:
    now = now or dt.datetime.now(dt.UTC)
    for scope, window, limit in LIMITS:
        if scope == "sender" and not address:
            continue
        key = sender_key(tenant_id, address) if scope == "sender" else "tenant"
        start = dt.datetime.fromtimestamp(int(now.timestamp()) // window * window, dt.UTC)
        hits = tx.scalar(
            """INSERT INTO app.rate_limits (tenant_id, bucket_key, window_start, hits)
               VALUES (app.current_tenant(), :k, :w, 1)
               ON CONFLICT (tenant_id, bucket_key, window_start) DO UPDATE SET hits = app.rate_limits.hits + 1
               RETURNING hits""",
            {"k": f"{key}:{window}", "w": start},
        )
        if int(hits) > limit:
            return Decision(False, f"{scope}/{window}s", int(start.timestamp()) + window - int(now.timestamp()))
    return Decision(True)
