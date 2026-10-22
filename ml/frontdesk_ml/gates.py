"""Quality gates: fail the build when a metric falls below its floor in docs/PLAN.md.

The floors live in the plan, between the quality-floors markers, so the number a reviewer
reads is the number CI enforces. Lowering a floor is a reviewed change to docs/PLAN.md.

    python -m frontdesk_ml.gates --pipeline offline --metrics reports/ci/metrics.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "PLAN.md"
# Metrics where lower is better: the floor is a ceiling.
CEILINGS = {"pii_reaching_model", "redteam_attack_success_rate"}


def floors(pipeline: str, plan: pathlib.Path = PLAN) -> dict[str, float]:
    text = plan.read_text(encoding="utf-8")
    m = re.search(r"<!-- quality-floors:start -->\s*```yaml\n(.*?)```\s*<!-- quality-floors:end -->", text, re.S)
    if not m:
        raise SystemExit("quality floors block not found in docs/PLAN.md")
    return {k: float(v) for k, v in yaml.safe_load(m.group(1))[pipeline].items()}


def check(metrics: dict[str, float], fl: dict[str, float]) -> list[str]:
    failures = []
    for name, floor in fl.items():
        if name not in metrics or metrics[name] is None:
            failures.append(f"{name}: not measured (floor {floor})")
            continue
        v = float(metrics[name])
        bad = v > floor if name in CEILINGS else v < floor
        if bad:
            failures.append(f"{name}: {v:.3f} {'above ceiling' if name in CEILINGS else 'below floor'} {floor}")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True, choices=["offline", "bedrock"])
    ap.add_argument("--metrics", required=True)
    a = ap.parse_args(argv)
    metrics = json.loads(pathlib.Path(a.metrics).read_text())["headline"]
    fl = floors(a.pipeline)
    failures = check(metrics, fl)
    for k in fl:
        mark = "FAIL" if any(f.startswith(k + ":") for f in failures) else "ok  "
        print(f"{mark} {k:32s} {metrics.get(k)!s:>8}  floor {fl[k]}")
    if failures:
        print("\nQUALITY GATE FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    print("\nquality gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
