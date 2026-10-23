"""The ROI sheet keeps measured and assumed benefits apart and never hides the low case."""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

from frontdesk_ml import roi

ROOT = pathlib.Path(__file__).resolve().parents[2]
INPUTS = ROOT / "docs" / "engagement" / "roi-inputs.demo.yaml"


def _rows(answered: int, n: int, leads: int):
    rows = [{"action": "answer" if i < answered else "handoff", "after_hours": i < leads, "intent": "pricing"} for i in range(n)]
    return rows


def test_the_cases_are_ordered_and_the_low_case_counts_no_extra_sales():
    r = roi.compute(roi.load(INPUTS), _rows(120, 200, 50))
    lo, mid, hi = (r["cases"][c] for c in roi.CASES)
    assert lo["benefit_zar"] <= mid["benefit_zar"] <= hi["benefit_zar"]
    assert lo["extra_margin_zar"] == 0
    assert r["answered_per_month"]["low"] < 120 < r["answered_per_month"]["high"]  # the answered share carries its interval


def test_an_assumption_without_a_source_is_refused(tmp_path):
    cfg = yaml.safe_load(INPUTS.read_text())
    cfg["assumptions"]["gross_margin_per_sale_zar"]["source"] = ""
    bad = tmp_path / "roi-inputs.bad.yaml"
    bad.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="needs a source"):
        roi.load(bad)


def test_the_demo_sheet_says_plainly_when_the_low_case_does_not_pay():
    cfg = roi.load(INPUTS)
    rows = [json.loads(line) for line in (ROOT / cfg["replay"]).read_text().splitlines()]
    r = roi.compute(cfg, rows)
    text = roi.sheet(cfg, r)
    assert r["cases"]["low"]["net_zar"] < 0 and "The low case does not pay" in text
