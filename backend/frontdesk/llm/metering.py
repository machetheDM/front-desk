"""Per-tenant model spend, metered in the app and capped.

Claude on Bedrock bills as AWS Marketplace line items, which a service-filtered AWS budget
does not catch (infra/lib/org/budgets-stack.ts), so the cap lives here: once a tenant's
month-to-date model cost passes FRONTDESK_MONTHLY_MODEL_CAP_USD, new questions are handed to
staff (reason cost_cap) instead of answered. Prices come from the Price List snapshot.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import os
import pathlib

from ..db import Transaction
from .base import Usage

SNAPSHOT = pathlib.Path(__file__).resolve().parents[3] / "docs" / "evidence" / "pricing-snapshot.json"

# USD per 1M tokens, keyed by a substring of the model id. Mirrors the snapshot; a test
# asserts they agree, so a price update cannot silently skip this table.
PRICES = {
    "claude-haiku-5-5": {"in": 0.11, "out": 0.55, "cache_read": 0.011},
    "claude-sonnet-5-5": {"in": 2.2, "out": 11.0, "cache_read": 0.11},
}


def cap_usd() -> float:
    return float(os.environ.get("FRONTDESK_MONTHLY_MODEL_CAP_USD", "8.0"))


def cost_usd(model_id: str, u: Usage) -> float:
    p = next((v for k, v in PRICES.items() if k in model_id), None)
    if p is None:
        return 0.0
    fresh = max(u.input_tokens - u.cached_tokens, 0)
    return (fresh * p["in"] + u.cached_tokens * p["cache_read"] + u.output_tokens * p["out"]) / 1_000_000


def _month(now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now(dt.UTC)
    return dt.date(now.year, now.month, 1)


def record(tx: Transaction, model_id: str, u: Usage, now: dt.datetime | None = None) -> float:
    c = cost_usd(model_id, u)
    tx.execute(
        """INSERT INTO app.usage_meter (tenant_id, month, model, calls, input_tokens, output_tokens, cached_tokens, cost_usd_micros)
           VALUES (app.current_tenant(), :m, :model, :calls, :i, :o, :c, :cost)
           ON CONFLICT (tenant_id, month, model) DO UPDATE SET
             calls = app.usage_meter.calls + EXCLUDED.calls,
             input_tokens = app.usage_meter.input_tokens + EXCLUDED.input_tokens,
             output_tokens = app.usage_meter.output_tokens + EXCLUDED.output_tokens,
             cached_tokens = app.usage_meter.cached_tokens + EXCLUDED.cached_tokens,
             cost_usd_micros = app.usage_meter.cost_usd_micros + EXCLUDED.cost_usd_micros""",
        {"m": _month(now), "model": model_id, "calls": u.calls, "i": u.input_tokens, "o": u.output_tokens,
         "c": u.cached_tokens, "cost": int(round(c * 1_000_000))},
    )
    return c


def month_to_date_usd(tx: Transaction, now: dt.datetime | None = None) -> float:
    v = tx.scalar("SELECT coalesce(sum(cost_usd_micros), 0) FROM app.usage_meter WHERE month = :m", {"m": _month(now)})
    return int(v) / 1_000_000


def over_cap(tx: Transaction, now: dt.datetime | None = None) -> bool:
    return month_to_date_usd(tx, now) >= cap_usd()


@functools.cache
def snapshot_prices() -> dict:
    return json.loads(SNAPSHOT.read_text())["prices"] if SNAPSHOT.exists() else {}
