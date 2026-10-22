"""Model spend is metered against Price List prices and capped; routing prefers the small model."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from frontdesk.llm import metering, router
from frontdesk.llm.base import Usage
from frontdesk.retrieval.search import Hit


def test_the_price_table_matches_the_aws_price_list_snapshot():
    snap = json.loads(metering.SNAPSHOT.read_text())["prices"]
    assert metering.PRICES["claude-haiku-5-5"] == {"in": snap["haiku55_in_mtok"]["usd"], "out": snap["haiku55_out_mtok"]["usd"],
                                                    "cache_read": snap["haiku55_cache_read_mtok"]["usd"]}
    assert metering.PRICES["claude-sonnet-5-5"]["in"] == snap["sonnet55_in_mtok"]["usd"]
    assert metering.PRICES["claude-sonnet-5-5"]["out"] == snap["sonnet55_out_mtok"]["usd"]


def test_cached_tokens_are_charged_at_the_cache_price():
    u = Usage(input_tokens=2_600, output_tokens=250, cached_tokens=900, calls=1)
    expected = (1_700 * 0.11 + 900 * 0.011 + 250 * 0.55) / 1_000_000
    assert metering.cost_usd("eu.anthropic.claude-haiku-5-5", u) == pytest.approx(expected)


@pytest.mark.db
def test_a_tenant_over_its_monthly_cap_is_detected(db, two_tenants, monkeypatch):
    a, _ = two_tenants
    monkeypatch.setenv("FRONTDESK_MONTHLY_MODEL_CAP_USD", "0.001")
    now = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
    with db.transaction(a.tenant_id) as tx:
        assert not metering.over_cap(tx, now)
        metering.record(tx, "eu.anthropic.claude-sonnet-5-5", Usage(10_000, 2_000, 0, 1), now)
        assert metering.over_cap(tx, now)


def test_routing_uses_the_small_model_unless_the_question_needs_more():
    hit = [Hit("c", "d", "t", "h", "x", rerank=3.0)]
    assert router.choose("How much is the 3kW?", hit).tier == "small"
    assert router.choose("What's the difference between the 5kW backup and the hybrid?", hit).tier == "large"
    assert router.choose("How much is the 3kW?", hit, attempt=1).reason == "retry after validation failure"
