"""Hour-of-week message volume forecast, with honest intervals and a backtest.

The owner's question is "when will messages arrive that nobody is in the office to answer?".
Three methods are compared by rolling-origin backtest (train on weeks 1..t, forecast week t+1):

- seasonal naive: next week equals last week, hour by hour. The baseline everything must beat.
- four-week mean: the average of the last four weeks at each hour of the week.
- level x profile: next week's total from Holt's linear smoothing of weekly totals, spread over
  the 168 hours by the recent hour-of-week profile, shrunk towards the weekday / Saturday /
  Sunday average so a single odd hour does not become the forecast.

Scores: MAE per hour, MASE (MAE relative to seasonal naive; below 1 beats it), error on the
weekly total, and how often the 80% interval contains the actual count. Intervals are negative
binomial with the over-dispersion measured on the training weeks, because counts like these
vary more than Poisson (load-shedding evenings).
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
from collections.abc import Callable

import numpy as np
from scipy import stats

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "bushveld" / "volume-hourly.synthetic.csv"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_weeks(path: pathlib.Path = DEMO) -> np.ndarray:
    rows = list(csv.DictReader(path.open()))
    first = dt.datetime.strptime(rows[0]["hour_start"], "%Y-%m-%d %H:%M")
    if first.weekday() != 0 or first.hour != 0 or len(rows) % 168:
        raise ValueError("expects whole weeks of hourly counts starting Monday 00:00")
    return np.asarray([int(r["messages"]) for r in rows], dtype=float).reshape(-1, 168)


def seasonal_naive(train: np.ndarray) -> np.ndarray:
    return train[-1].copy()


def four_week_mean(train: np.ndarray) -> np.ndarray:
    return train[-4:].mean(axis=0)


def _day_type(h: int) -> int:
    d = h // 24
    return 0 if d < 5 else d - 4  # weekday, Saturday, Sunday


def level_profile(train: np.ndarray, alpha: float = 0.5, beta: float = 0.2, pseudo: float = 60.0, recent: int = 8) -> np.ndarray:
    totals = train.sum(axis=1)
    level, trend = totals[0], 0.0
    for y in totals[1:]:
        prev = level
        level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (level - prev) + (1 - beta) * trend
    next_total = max(level + trend, 0.0)
    window = train[-recent:]
    counts = window.sum(axis=0)
    # Prior share for hour h: the mean share of that hour-of-day on days of the same type.
    share = counts / max(counts.sum(), 1)
    prior = np.zeros(168)
    for t in range(3):
        days = [d for d in range(7) if _day_type(d * 24) == t]
        by_hour = np.mean([share[d * 24:(d + 1) * 24] for d in days], axis=0)
        for d in days:
            prior[d * 24:(d + 1) * 24] = by_hour
    prior /= prior.sum()
    shrunk = (counts + pseudo * prior) / (counts.sum() + pseudo)
    return next_total * shrunk


METHODS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "seasonal naive": seasonal_naive, "four-week mean": four_week_mean, "level x profile": level_profile}


def dispersion(train: np.ndarray, method: Callable, lookback: int = 4) -> float:
    """Variance-to-mean ratio of one-week-ahead errors inside the training data (>= 1)."""
    ratios = []
    for t in range(max(4, len(train) - lookback), len(train)):
        mu = method(train[:t])
        y = train[t]
        m = mu > 0.2
        ratios.extend(((y[m] - mu[m]) ** 2 / mu[m]).tolist())
    return max(float(np.mean(ratios)) if ratios else 1.0, 1.0)


def interval(mu: np.ndarray, phi: float, level: float = 0.80) -> tuple[np.ndarray, np.ndarray]:
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    mu = np.maximum(mu, 1e-6)
    if phi <= 1.0001:
        return stats.poisson.ppf(lo_q, mu), stats.poisson.ppf(hi_q, mu)
    n, p = mu / (phi - 1), 1 / phi
    return stats.nbinom.ppf(lo_q, n, p), stats.nbinom.ppf(hi_q, n, p)


def backtest(weeks: np.ndarray, folds: int = 4) -> dict[str, dict]:
    out = {}
    naive_mae = []
    for t in range(len(weeks) - folds, len(weeks)):
        naive_mae.append(np.abs(weeks[t] - seasonal_naive(weeks[:t])).mean())
    for name, f in METHODS.items():
        mae, tot, cover, busy = [], [], [], []
        for t in range(len(weeks) - folds, len(weeks)):
            train, y = weeks[:t], weeks[t]
            mu = f(train)
            lo, hi = interval(mu, dispersion(train, f))
            mae.append(np.abs(y - mu).mean())
            tot.append(abs(mu.sum() - y.sum()) / y.sum())
            inside = (y >= lo) & (y <= hi)
            cover.append(inside.mean())
            busy.append(inside[mu >= 1.5].mean())  # at tiny counts any interval "covers"; the busy hours test it
        out[name] = {"mae_per_hour": round(float(np.mean(mae)), 3), "mase": round(float(np.mean(mae) / np.mean(naive_mae)), 3),
                     "weekly_total_error": round(float(np.mean(tot)), 3), "coverage_80": round(float(np.mean(cover)), 3),
                     "coverage_80_busy_hours": round(float(np.mean(busy)), 3)}
    return out


def office_open(h: int, hours: dict | None = None) -> bool:
    """Bushveld's published hours (demo/bushveld/docs/company-info.md): Mon-Fri 08-17, Sat 08-12."""
    d, hr = divmod(h, 24)
    return (d < 5 and 8 <= hr < 17) or (d == 5 and 8 <= hr < 12)


def next_week(weeks: np.ndarray, method: str = "level x profile") -> dict:
    f = METHODS[method]
    mu = f(weeks)
    phi = dispersion(weeks, f)
    # The weekly total's interval: sum of hours, variance phi * mu summed (independent hours).
    total, sd = float(mu.sum()), float(np.sqrt(phi * mu.sum()))
    closed = float(sum(mu[h] for h in range(168) if not office_open(h)))
    busiest = sorted(range(168), key=lambda h: -mu[h])[:3]
    return {"method": method, "total": round(total), "total_80": [round(max(total - 1.2816 * sd, 0)), round(total + 1.2816 * sd)],
            "closed_share": round(closed / total, 3) if total else 0.0,
            "busiest": [f"{DAYS[h // 24]} {h % 24:02d}:00 (about {mu[h]:.1f})" for h in busiest], "dispersion": round(phi, 2)}
