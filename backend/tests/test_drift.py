"""Weekly drift on the intent mix: PSI from events, with a floor below which it stays quiet."""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter

import pytest

from frontdesk import drift
from frontdesk.tenancy import create_tenant

NOW = dt.datetime(2026, 10, 26, 5, 0, tzinfo=dt.UTC)


def test_psi_is_near_zero_for_the_same_mix_and_grows_with_the_shift():
    base = Counter(pricing=40, warranty=30, hours=20, fault=10)
    same, _ = drift.psi(base, Counter(pricing=80, warranty=60, hours=40, fault=20))
    small, _ = drift.psi(base, Counter(pricing=38, warranty=30, hours=20, fault=12))
    big, parts = drift.psi(base, Counter(pricing=20, warranty=20, hours=10, fault=50))
    assert same < 0.001 and same < small < drift.WATCH and big > drift.SHIFTED
    assert parts[0]["intent"] == "fault"  # the intent that moved most is named first


def test_an_intent_that_appears_from_nowhere_is_scored_not_crashed():
    value, parts = drift.psi(Counter(pricing=100), Counter(pricing=50, ev_charger=50))
    assert value > drift.SHIFTED and parts[0]["intent"] == "ev_charger"


def test_rare_intents_absent_this_week_do_not_make_a_shift_out_of_noise():
    base = Counter(pricing=120, warranty=80, hours=60, location=4, complaint=3, fault=3, refund=2)
    week = Counter(pricing=31, warranty=19, hours=15)  # the rare ones simply did not come up
    r = drift.score(base, week)
    assert r["level"] == "stable", r


def test_a_shift_needs_both_a_large_psi_and_a_small_p_value():
    assert drift.level(0.9, 0.2, 200) == "stable"       # big-looking but could be chance
    assert drift.level(0.05, 1e-6, 5000) == "stable"    # certainly real but too small to matter
    assert drift.level(0.3, 0.001, 200) == "shifted"
    assert drift.level(0.9, 1e-9, drift.MIN_MESSAGES - 1) == "too few to judge"


def test_the_chi_square_tail_matches_scipy_to_within_a_hundredth():
    stats = pytest.importorskip("scipy.stats")
    base, cur = Counter(a=50, b=30, c=20, d=25), Counter(a=10, b=12, c=4, d=9)
    b2, c2 = drift.collapse(base, cur)
    table = [[b2[k] for k in sorted(b2)], [c2.get(k, 0) for k in sorted(b2)]]
    exact = stats.chi2_contingency(table, correction=False)[1]
    assert abs(drift.chi_square_p(base, cur) - exact) < 0.01


def _seed(db, tenant, mix: dict[str, int], days_ago: float) -> None:
    with db.transaction(tenant.tenant_id) as tx:
        cust = tx.scalar("INSERT INTO app.customers (tenant_id, channel, channel_address) VALUES (app.current_tenant(), 'web', :a) RETURNING customer_id::text",
                         {"a": f"web:{uuid.uuid4().hex}"})
        conv = tx.scalar("INSERT INTO app.conversations (tenant_id, customer_id, channel) VALUES (app.current_tenant(), :c, 'web') RETURNING conversation_id::text",
                         {"c": cust})
        for intent, n in mix.items():
            for i in range(n):
                tx.execute("""INSERT INTO app.conversation_events (tenant_id, conversation_id, type, payload, occurred_at)
                              VALUES (app.current_tenant(), :c, 'answer_sent', :p::jsonb, :t)""",
                           {"c": conv, "p": {"intent": intent}, "t": NOW - dt.timedelta(days=days_ago, minutes=i)})


@pytest.mark.db
def test_the_weekly_job_reads_events_and_names_the_intents_that_moved(db):
    t = create_tenant(db, "Drift Test Electrical", f"drift-{uuid.uuid4().hex[:6]}")
    for week in (1.5, 2.5, 3.5, 4.5):  # four baseline weeks: mostly pricing and hours
        _seed(db, t, {"pricing": 30, "hours": 15, "warranty": 10, "fault": 5}, days_ago=week * 7)
    _seed(db, t, {"pricing": 15, "hours": 10, "warranty": 10, "fault": 35}, days_ago=2)  # load-shedding week
    with db.transaction(t.tenant_id) as tx:
        r = drift.weekly(tx, NOW)
    assert r["current_messages"] == 70 and r["baseline_messages"] == 240
    assert r["level"] == "shifted" and r["top_movers"][0]["intent"] == "fault"


@pytest.mark.db
def test_another_tenants_events_never_enter_the_score(db, two_tenants):
    a, b = two_tenants
    _seed(db, a, {"pricing": 60}, days_ago=10)
    _seed(db, a, {"pricing": 60}, days_ago=2)
    _seed(db, b, {"fault": 500}, days_ago=2)
    with db.transaction(a.tenant_id) as tx:
        r = drift.weekly(tx, NOW)
    assert r["psi"] < 0.001 and r["level"] == "stable" and r["current_messages"] == 60
