"""The quality gate reads the floors from docs/PLAN.md and fails below them; metrics are right."""

from __future__ import annotations

import json
import pathlib

import pytest

from frontdesk_ml import gates, judge, metrics

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_floors_are_read_from_the_plan_for_both_pipelines():
    off, bed = gates.floors("offline"), gates.floors("bedrock")
    assert off["unanswerable_handed_over"] > 0 and bed["judge_cohen_kappa"] == 0.6
    assert off["redteam_attack_success_rate"] == 0.0


def test_metrics_just_below_the_floors_fail_the_gate():
    m = json.loads((FIX / "metrics_below_floor.json").read_text())["headline"]
    failures = gates.check(m, gates.floors("offline"))
    assert len(failures) == len(gates.floors("offline"))


def test_lower_is_better_metrics_are_ceilings():
    assert gates.check({"pii_reaching_model": 0}, {"pii_reaching_model": 0.0}) == []
    assert gates.check({"pii_reaching_model": 1}, {"pii_reaching_model": 0.0})


def test_an_unmeasured_metric_fails_rather_than_passing_silently():
    assert gates.check({}, {"judge_cohen_kappa": 0.6}) == ["judge_cohen_kappa: not measured (floor 0.6)"]


def test_recall_and_mrr_on_a_known_example():
    ranks = [1, 3, None, 6]
    assert metrics.recall_at_k(ranks, 5) == 0.5
    assert metrics.mrr(ranks) == pytest.approx((1 + 1 / 3 + 0 + 1 / 6) / 4)


def test_cohens_kappa_corrects_for_chance():
    # Both raters say "correct" 90% of the time and agree 82%: kappa is 0, not 0.82.
    a = [True] * 9 + [False]
    b = [True] * 8 + [False, True]
    assert judge.cohen_kappa(a, b) == pytest.approx((0.8 - 0.82) / (1 - 0.82))
    assert judge.cohen_kappa([True, False, True], [True, False, True]) == 1.0


def test_calibration_says_plainly_when_there_are_not_enough_human_labels(tmp_path):
    out = judge.calibrate({"q1": True}, "offline", tmp_path / "none.yaml")
    assert out["kappa"] is None and "needs at least 30" in out["status"]


def test_facts_match_ignores_spacing_and_case():
    assert metrics.facts_ok("It costs r62900 installed", ["R62 900"], [])
    assert not metrics.facts_ok("R62 900 or R89 500", ["R62 900"], ["R89 500"])
