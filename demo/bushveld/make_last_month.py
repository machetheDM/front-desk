"""Builds last-month.synthetic.csv: 212 made-up WhatsApp questions over September 2026, for the
replay demo. Deterministic (fixed seed). Volume skews to evenings, as installer WhatsApps do.

    python demo/bushveld/make_last_month.py
"""

import csv
import datetime as dt
import pathlib
import random

import yaml

HERE = pathlib.Path(__file__).parent
random.seed(20261022)
items = yaml.safe_load((HERE.parents[1] / "evals/datasets/bushveld.synthetic.yaml").read_text())
texts = [i["text"] for i in items if not i.get("small_talk")]
names = ["Lerato", "Thabo", "Karabo", "Pieter", "Nomsa", "Johan", "Mpho", "", "", "Tshepo", "Anel", "Sipho"]
start = dt.datetime(2026, 9, 1)
rows = []
for n in range(212):
    day = start + dt.timedelta(days=random.randint(0, 29))
    hour = random.choice([7, 8, 9, 10, 12, 13, 15, 16] if random.random() < 0.4 else [18, 19, 20, 21, 22, 6, 23])
    t = day.replace(hour=hour, minute=random.randint(0, 59))
    rows.append({"id": f"s{n + 1:04d}", "received_at": t.strftime("%Y-%m-%d %H:%M"), "question": random.choice(texts),
                 "customer_name": random.choice(names), "channel_address": ""})
rows.sort(key=lambda r: r["received_at"])
with (HERE / "last-month.synthetic.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"wrote {len(rows)} rows")
