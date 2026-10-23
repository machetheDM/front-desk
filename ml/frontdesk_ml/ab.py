"""A/B tests of prompt versions: plan the sample size, refuse to peek, analyse once.

    python -m frontdesk_ml.ab plan experiments/exp-2026-11-lead-with-answer.yaml --per-week 50
    python -m frontdesk_ml.ab analyse experiments/exp-2026-11-lead-with-answer.yaml --counts counts.json
    python -m frontdesk_ml.ab simulate-peeking

The pre-registration file fixes the hypothesis, metric, minimum detectable effect, alpha,
power and sample size *before* any data exists; `admin start-experiment` stores its SHA-256,
so it cannot be edited after the fact. Analysis refuses to run before the planned sample is
reached, because looking repeatedly and stopping at the first p < 0.05 turns a 5% false
positive rate into roughly 20% or more (`simulate-peeking` shows it). Stopping early is allowed
only for harm (a guardrail breach), never for success.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import sys
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import yaml

from frontdesk.stats import wilson

ROOT = pathlib.Path(__file__).resolve().parents[2]
Z = NormalDist()


def sample_size_per_arm(p_control: float, mde: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Two-sided test of two proportions (normal approximation, unpooled under H1)."""
    p1, p2 = p_control, p_control + mde
    if not (0 < p1 < 1 and 0 < p2 < 1) or mde == 0:
        raise ValueError("proportions must stay inside (0, 1) and the effect must be non-zero")
    pbar = (p1 + p2) / 2
    za, zb = Z.inv_cdf(1 - alpha / 2), Z.inv_cdf(power)
    n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / mde**2
    return math.ceil(n)


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    z = (x2 / n2 - x1 / n1) / se if se > 0 else 0.0
    return z, 2 * (1 - Z.cdf(abs(z)))


def newcombe_diff(x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05) -> tuple[float, float, float]:
    """95% CI for p2 - p1 from two Wilson intervals (Newcombe 1998, method 10): it behaves at
    small n and near 0 or 1, where the plain Wald interval undercovers."""
    p1, l1, u1 = wilson(x1, n1, Z.inv_cdf(1 - alpha / 2))
    p2, l2, u2 = wilson(x2, n2, Z.inv_cdf(1 - alpha / 2))
    d = p2 - p1
    return d, d - math.sqrt((p2 - l2) ** 2 + (u1 - p1) ** 2), d + math.sqrt((u2 - p2) ** 2 + (p1 - l1) ** 2)


@dataclass
class Prereg:
    path: pathlib.Path
    data: dict

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def n_per_arm(self) -> int:
        m = self.data["primary_metric"]
        return sample_size_per_arm(m["baseline"], m["minimum_detectable_effect"], self.data["alpha"], self.data["power"])


def load_prereg(path: pathlib.Path | str) -> Prereg:
    path = pathlib.Path(path)
    return Prereg(path, yaml.safe_load(path.read_text(encoding="utf-8")))


def prereg_problems(pr: Prereg) -> list[str]:
    d, out = pr.data, []
    for k in ("id", "status", "hypothesis", "unit", "arms", "primary_metric", "alpha", "power", "sample_size_per_arm", "guardrails", "stopping", "decision_rule"):
        if k not in d:
            out.append(f"missing {k}")
    if out:
        return out
    if pr.path.stem != d["id"]:
        out.append("file name must be the experiment id")
    if d["status"] not in ("draft", "approved", "running", "stopped", "analysed"):
        out.append("unknown status")
    m = d["primary_metric"]
    for k in ("name", "definition", "baseline", "baseline_source", "minimum_detectable_effect"):
        if k not in m:
            out.append(f"primary_metric missing {k}")
    if not out and d["sample_size_per_arm"] != pr.n_per_arm:
        out.append(f"sample_size_per_arm says {d['sample_size_per_arm']} but the stated baseline, effect, alpha and power need {pr.n_per_arm}")
    return out


def analyse(pr: Prereg, counts: dict) -> dict:
    """The single, pre-registered analysis. Returns a verdict; never a number before its time."""
    need = pr.n_per_arm
    c, t = counts["control"], counts["treatment"]
    if min(c["exposed"], t["exposed"]) < need:
        return {"verdict": "not yet", "reason": f"{c['exposed']} control and {t['exposed']} treatment of {need} per arm. "
                "Analysing now inflates the false positive rate (python -m frontdesk_ml.ab simulate-peeking).",
                "guardrail_breach": _guardrail_breach(pr, c, t)}
    x1, n1, x2, n2 = c["resolved_without_staff"], c["exposed"], t["resolved_without_staff"], t["exposed"]
    z, p = two_proportion_z(x1, n1, x2, n2)
    d, lo, hi = newcombe_diff(x1, n1, x2, n2, pr.data["alpha"])
    breach = _guardrail_breach(pr, c, t)
    if breach:
        verdict = "keep control: guardrail breached"
    elif p < pr.data["alpha"] and d > 0:
        verdict = "ship treatment"
    elif p < pr.data["alpha"] and d < 0:
        verdict = "keep control: treatment is worse"
    else:
        verdict = "keep control: no detectable difference"
    return {"verdict": verdict, "control_rate": x1 / n1, "treatment_rate": x2 / n2, "difference": d, "ci95": [lo, hi],
            "z": z, "p_value": p, "n_per_arm_planned": need, "guardrail_breach": breach}


def _guardrail_breach(pr: Prereg, c: dict, t: dict) -> str | None:
    limit = pr.data["guardrails"]["asked_for_person_max_increase"]
    if min(c["exposed"], t["exposed"]) < 30:
        return None  # too few to judge harm either way
    rc, rt = c["asked_for_person"] / c["exposed"], t["asked_for_person"] / t["exposed"]
    return f"asked-for-person rose {rt - rc:+.3f} (limit +{limit})" if rt - rc > limit else None


def simulate_peeking(p: float = 0.8, n_per_arm: int = 900, looks: int = 10, sims: int = 4000, alpha: float = 0.05,
                     seed: int = 20261023) -> dict:
    """A/A test (no real difference): how often does each policy declare a winner?"""
    rng = np.random.default_rng(seed)
    a = rng.random((sims, n_per_arm)) < p
    b = rng.random((sims, n_per_arm)) < p
    checkpoints = np.linspace(n_per_arm / looks, n_per_arm, looks).astype(int)
    za = Z.inv_cdf(1 - alpha / 2)

    def significant(n: int) -> np.ndarray:
        x1, x2 = a[:, :n].sum(1), b[:, :n].sum(1)
        pool = (x1 + x2) / (2 * n)
        se = np.sqrt(pool * (1 - pool) * 2 / n)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.where(se > 0, (x2 - x1) / n / se, 0.0)
        return np.abs(z) > za

    fixed = significant(n_per_arm).mean()
    peeked = np.zeros(sims, dtype=bool)
    for n in checkpoints:
        peeked |= significant(int(n))
    return {"p": p, "n_per_arm": n_per_arm, "looks": looks, "sims": sims,
            "false_positive_fixed_horizon": float(fixed), "false_positive_with_peeking": float(peeked.mean())}


def plan_text(pr: Prereg, per_week: float) -> str:
    n = pr.n_per_arm
    weeks = 2 * n / per_week if per_week > 0 else math.inf
    m = pr.data["primary_metric"]
    lines = [f"{pr.data['id']}: {m['name']} from {m['baseline']:.2f} by {m['minimum_detectable_effect']:+.2f} "
             f"(alpha {pr.data['alpha']}, power {pr.data['power']})",
             f"needs {n} customers per arm, {2 * n} in total; at {per_week:.0f} answered customers a week that is {weeks:.0f} weeks."]
    for mde in (0.05, 0.10, 0.15):
        try:
            k = sample_size_per_arm(m["baseline"], mde, pr.data["alpha"], pr.data["power"])
            lines.append(f"  if the effect to detect were {mde:+.2f}: {k} per arm, {2 * k / per_week:.0f} weeks")
        except ValueError:
            pass
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "simulate-peeking"
    if cmd == "simulate-peeking":
        print(json.dumps(simulate_peeking(), indent=2))
        return 0
    pr = load_prereg(argv[1])
    if probs := prereg_problems(pr):
        print("PRE-REGISTRATION INVALID:\n  " + "\n  ".join(probs), file=sys.stderr)
        return 1
    if cmd == "plan":
        per_week = float(argv[argv.index("--per-week") + 1]) if "--per-week" in argv else 50.0
        print(plan_text(pr, per_week))
        print(f"pre-registration sha256 {pr.sha256}")
        return 0
    if cmd == "analyse":
        counts = json.loads(pathlib.Path(argv[argv.index("--counts") + 1]).read_text())
        print(json.dumps(analyse(pr, counts), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
