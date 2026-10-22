"""Label answers by hand, so the judge can be calibrated against a person (Cohen's kappa).

Shows each answered question from an eval run with the facts a correct answer needs, and asks
you: correct or not? Appends to evals/labels/human-judgments.yaml. Thirty labels per pipeline
is the minimum before a kappa is reported. Takes about 15 minutes.

    python -m frontdesk_ml.label --replay reports/local/replay.jsonl --pipeline offline --labeller "Machethe DM"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

import yaml

from . import dataset
from .judge import HUMAN_LABELS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--pipeline", required=True, choices=["offline", "bedrock"])
    ap.add_argument("--labeller", required=True)
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args(argv)
    items = {i.id: i for i in dataset.load() + dataset.load(dataset.HOLDOUT)}
    existing = yaml.safe_load(HUMAN_LABELS.read_text()) if HUMAN_LABELS.exists() else []
    done = {(r["item_id"], r["pipeline"]) for r in existing or []}
    rows = [json.loads(line) for line in pathlib.Path(a.replay).read_text().splitlines()]
    todo = [r for r in rows if r["answered"] and r["label_action"] == "answer" and (r["id"], a.pipeline) not in done][: a.limit]
    new = []
    for n, r in enumerate(todo, 1):
        it = items.get(r["id"])
        print(f"\n[{n}/{len(todo)}] {r['id']}  Q: {r['question_redacted']}")
        print(f"  A: {r['reply']}")
        print(f"  A correct answer must state: {', '.join(it.facts) if it else '?'}")
        ans = input("  Correct? [y/n/s=skip/q=quit] ").strip().lower()
        if ans == "q":
            break
        if ans in ("y", "n"):
            new.append({"item_id": r["id"], "pipeline": a.pipeline, "verdict": "correct" if ans == "y" else "incorrect",
                        "labeller": a.labeller, "date": dt.date.today().isoformat()})
    HUMAN_LABELS.parent.mkdir(parents=True, exist_ok=True)
    HUMAN_LABELS.write_text(yaml.safe_dump((existing or []) + new, sort_keys=False))
    print(f"\nsaved {len(new)} labels to {HUMAN_LABELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
