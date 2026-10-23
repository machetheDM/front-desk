"""A/B statistics: right sample sizes, honest intervals, and why the tool refuses to peek."""

from __future__ import annotations

import pathlib

import numpy as np

from frontdesk_ml import ab

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_sample_sizes_match_the_textbook_values():
    assert ab.sample_size_per_arm(0.50, 0.10) == 388   # the standard 50% -> 60% example
    assert ab.sample_size_per_arm(0.80, 0.05) == 906
    assert ab.sample_size_per_arm(0.80, 0.10) < ab.sample_size_per_arm(0.80, 0.05) / 4  # quarter the n for double the effect, roughly


def test_the_newcombe_interval_covers_the_true_difference_about_95_percent_of_the_time():
    rng = np.random.default_rng(7)
    p1, p2, n, hits, sims = 0.80, 0.85, 300, 0, 2000
    for _ in range(sims):
        x1, x2 = rng.binomial(n, p1), rng.binomial(n, p2)
        _, lo, hi = ab.newcombe_diff(int(x1), n, int(x2), n)
        hits += lo <= p2 - p1 <= hi
    assert 0.93 <= hits / sims <= 0.97


def test_peeking_inflates_false_positives_and_a_fixed_horizon_does_not():
    r = ab.simulate_peeking(sims=3000)
    assert r["false_positive_fixed_horizon"] < 0.065
    assert r["false_positive_with_peeking"] > 2.5 * r["false_positive_fixed_horizon"]


def test_every_pre_registration_in_the_repo_is_complete_and_its_sample_size_is_right():
    files = sorted((ROOT / "experiments").glob("*.yaml"))
    assert files
    for f in files:
        assert ab.prereg_problems(ab.load_prereg(f)) == [], f.name


def test_a_pre_registration_with_a_wrong_sample_size_is_rejected(tmp_path):
    src = (ROOT / "experiments" / "exp-2026-11-lead-with-answer.yaml").read_text()
    bad = tmp_path / "exp-2026-11-lead-with-answer.yaml"
    bad.write_text(src.replace("sample_size_per_arm: 906", "sample_size_per_arm: 200"))
    assert any("need 906" in p for p in ab.prereg_problems(ab.load_prereg(bad)))


def _counts(n, x_c, x_t, person_c=0, person_t=0):
    return {"control": {"exposed": n, "resolved_without_staff": x_c, "asked_for_person": person_c},
            "treatment": {"exposed": n, "resolved_without_staff": x_t, "asked_for_person": person_t}}


def test_analysis_refuses_to_give_a_number_before_the_planned_sample():
    pr = ab.load_prereg(ROOT / "experiments" / "exp-2026-11-lead-with-answer.yaml")
    r = ab.analyse(pr, _counts(400, 300, 360))
    assert r["verdict"] == "not yet" and "p_value" not in r


def test_a_clear_win_ships_and_a_guardrail_breach_overrides_it():
    pr = ab.load_prereg(ROOT / "experiments" / "exp-2026-11-lead-with-answer.yaml")
    win = ab.analyse(pr, _counts(1000, 800, 870, 50, 52))
    assert win["verdict"] == "ship treatment" and win["ci95"][0] > 0
    harm = ab.analyse(pr, _counts(1000, 800, 870, 50, 90))
    assert harm["verdict"].startswith("keep control: guardrail")
    flat = ab.analyse(pr, _counts(1000, 800, 805))
    assert flat["verdict"] == "keep control: no detectable difference" and flat["ci95"][0] < 0 < flat["ci95"][1]
