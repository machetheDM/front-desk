"""Builds volume-hourly.synthetic.csv: twelve made-up weeks of hourly message counts for the
demo tenant, for the volume forecast. Deterministic (fixed seed). Not real data.

The pattern is what installers describe: quiet nights, a morning peak, a bigger evening peak
when people get home, Saturday mornings busy, Sundays light, slow growth, and load-shedding
evenings (unannounced, about one a week) that double the evening load. The forecast is not
told about load-shedding, so those evenings test its prediction intervals.

    python demo/bushveld/make_volume.py
"""

import csv
import datetime as dt
import math
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).parent
rng = np.random.default_rng(20261023)
start = dt.datetime(2026, 7, 27)  # a Monday, local time (Africa/Johannesburg)
WEEKS = 12


def rate(t: dt.datetime, week: int) -> float:
    h, wd = t.hour, t.weekday()
    base = 0.15 + 1.2 * math.exp(-((h - 8.5) ** 2) / 3) + 2.0 * math.exp(-((h - 19) ** 2) / 4) + 0.5 * math.exp(-((h - 12.5) ** 2) / 2)
    if wd == 5:
        base *= 1.3 if h < 13 else 0.7
    if wd == 6:
        base *= 0.45
    return base * (1 + 0.02 * week)


shedding_days = {d for d in range(WEEKS * 7) if rng.random() < 1 / 7}
rows = []
for d in range(WEEKS * 7):
    for h in range(24):
        t = start + dt.timedelta(days=d, hours=h)
        lam = rate(t, d // 7) * (2.0 if d in shedding_days and 17 <= h <= 21 else 1.0)
        rows.append({"hour_start": t.strftime("%Y-%m-%d %H:00"), "messages": int(rng.poisson(lam))})
with (HERE / "volume-hourly.synthetic.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["hour_start", "messages"])
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} hours, {sum(r['messages'] for r in rows)} messages, {len(shedding_days)} load-shedding evenings")
