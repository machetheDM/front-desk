"""Weekly drift on what customers ask about: population stability index (PSI) on the intent mix.

Why it matters here: the documents, the glossary and the evaluation set were built for the
questions customers asked last month. If the mix moves (load-shedding returns and fault
questions double, a new product line appears), quality can fall without any code change.
PSI compares this week's share of each intent with the previous four weeks':

    PSI = sum over intents of (current - baseline) * ln(current / baseline)

The textbook reading (below 0.10 stable, 0.25 or more shifted) assumes thousands of records.
A small business has tens of messages a week, and the first version of this module, which
floored empty intents at a tiny epsilon, scored PSI above 1 one week in twenty when nothing had
changed (docs/evidence/insights-demo.md section 4). Three changes make it usable at SME volume:

1. Intents below 5% in both windows are merged into "other", so a rare intent that happens to
   be absent this week does not dominate the score.
2. Counts are smoothed (half a message added to every intent in both windows) instead of
   floored, which keeps ln() finite without inventing huge ratios.
3. An alarm needs the change to be large (PSI) *and* unlikely to be sampling noise (a
   chi-square test of homogeneity between the two windows' counts). Effect size alone fires on
   noise at small n; a p-value alone fires on trivial shifts at large n.

`ml/frontdesk_ml/analyses.py` measures the false-alarm and detection rates of this rule by
simulation. Runs weekly per tenant from `handlers/ops.drift`; DET-12 alarms on "shifted".
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from statistics import NormalDist

from .db import Transaction

SMOOTHING = 0.5
MIN_SHARE = 0.05
MIN_MESSAGES = 30          # below this the chi-square approximation is not trustworthy
WATCH, SHIFTED = 0.10, 0.25
P_WATCH, P_SHIFTED = 0.05, 0.01


def collapse(baseline: Counter, current: Counter, min_share: float = MIN_SHARE) -> tuple[Counter, Counter]:
    nb, nc = sum(baseline.values()) or 1, sum(current.values()) or 1
    keep = {c for c in set(baseline) | set(current) if baseline.get(c, 0) / nb >= min_share or current.get(c, 0) / nc >= min_share}

    def fold(x: Counter) -> Counter:
        out = Counter()
        for c, v in x.items():
            out[c if c in keep else "other"] += v
        return out

    return fold(baseline), fold(current)


def psi(baseline: Counter, current: Counter) -> tuple[float, list[dict]]:
    b_c, c_c = collapse(baseline, current)
    cats = sorted(set(b_c) | set(c_c))
    nb = sum(b_c.values()) + SMOOTHING * len(cats)
    nc = sum(c_c.values()) + SMOOTHING * len(cats)
    parts = []
    for c in cats:
        b = (b_c.get(c, 0) + SMOOTHING) / nb
        a = (c_c.get(c, 0) + SMOOTHING) / nc
        parts.append({"intent": c, "baseline_share": round(b, 4), "current_share": round(a, 4), "contribution": (a - b) * math.log(a / b)})
    parts.sort(key=lambda p: -p["contribution"])
    total = sum(p["contribution"] for p in parts)
    for p in parts:
        p["contribution"] = round(p["contribution"], 4)
    return total, parts


def chi_square_p(baseline: Counter, current: Counter) -> float:
    """Homogeneity test on the collapsed 2 x k table. The upper tail uses the Wilson-Hilferty
    approximation (no scipy in the Lambda); it is accurate to about 0.005 for k >= 3, which is
    plenty for choosing between "noise" and "look at this"."""
    b_c, c_c = collapse(baseline, current)
    cats = sorted(set(b_c) | set(c_c))
    nb, nc = sum(b_c.values()), sum(c_c.values())
    n = nb + nc
    if len(cats) < 2 or nb == 0 or nc == 0:
        return 1.0
    x2 = 0.0
    for c in cats:
        col = b_c.get(c, 0) + c_c.get(c, 0)
        for obs, row in ((b_c.get(c, 0), nb), (c_c.get(c, 0), nc)):
            exp = row * col / n
            x2 += (obs - exp) ** 2 / exp if exp > 0 else 0.0
    k = len(cats) - 1
    z = ((x2 / k) ** (1 / 3) - (1 - 2 / (9 * k))) / math.sqrt(2 / (9 * k))
    return 1 - NormalDist().cdf(z)


def level(value: float, p_value: float, n_current: int) -> str:
    if n_current < MIN_MESSAGES:
        return "too few to judge"
    if value >= SHIFTED and p_value < P_SHIFTED:
        return "shifted"
    if value >= WATCH and p_value < P_WATCH:
        return "watch"
    return "stable"


def score(baseline: Counter, current: Counter) -> dict:
    value, parts = psi(baseline, current)
    p = chi_square_p(baseline, current)
    n_cur, n_base = sum(current.values()), sum(baseline.values())
    lvl = level(value, p, n_cur) if n_base >= MIN_MESSAGES else "too few to judge"
    return {"psi": round(value, 4), "p_value": round(p, 4), "level": lvl, "current_messages": n_cur, "baseline_messages": n_base,
            "top_movers": [x for x in parts if x["contribution"] > 0][:3]}


def intent_mix(tx: Transaction, since: dt.datetime, until: dt.datetime) -> Counter:
    """One count per customer message that got an outcome; the intent is in every outcome event."""
    rows = tx.execute(
        """SELECT payload->>'intent' AS intent, count(*) AS n FROM app.conversation_events
           WHERE type IN ('answer_sent', 'handed_over', 'degraded', 'refused') AND occurred_at >= :s AND occurred_at < :u
           GROUP BY 1""", {"s": since, "u": until})
    return Counter({r["intent"] or "general": int(r["n"]) for r in rows})


def weekly(tx: Transaction, now: dt.datetime | None = None, baseline_weeks: int = 4) -> dict:
    now = now or dt.datetime.now(dt.UTC)
    week_start = now - dt.timedelta(days=7)
    current = intent_mix(tx, week_start, now)
    baseline = intent_mix(tx, week_start - dt.timedelta(days=7 * baseline_weeks), week_start)
    r = score(baseline, current)
    r["window"] = {"current_from": week_start.isoformat(timespec="minutes"), "until": now.isoformat(timespec="minutes")}
    return r
