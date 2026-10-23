"""DORA arithmetic on synthetic deploy histories (tools/dora/dora.py)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("dora", pathlib.Path(__file__).resolve().parents[2] / "tools" / "dora" / "dora.py")
dora = importlib.util.module_from_spec(spec)
sys.modules["dora"] = dora  # dataclasses look their module up while the class is created
spec.loader.exec_module(dora)

T0 = dt.datetime(2026, 11, 2, 9, 0, tzinfo=dt.UTC)


def h(hours: float) -> dt.datetime:
    return T0 + dt.timedelta(hours=hours)


def test_the_four_keys_on_a_known_history():
    deploys = [
        dora.Deploy("a", h(10), True, [h(0), h(4)]),       # lead times 10 h and 6 h
        dora.Deploy("b", h(30), False),                     # canary rolled back
        dora.Deploy("c", h(33), True, [h(29)]),             # restored 3 h later; lead 4 h
        dora.Deploy("d", h(60), True, [h(58)]),             # lead 2 h
    ]
    m = dora.metrics(deploys, weeks=2)
    assert m["deployment_frequency_per_week"] == 1.5
    assert m["lead_time_hours_median"] == 5.0
    assert m["change_failure_rate"] == 0.25
    assert m["time_to_restore_hours_median"] == 3.0


def test_no_deploys_reports_nothing_rather_than_zero():
    m = dora.metrics([], weeks=12)
    assert m["lead_time_hours_median"] is None and m["change_failure_rate"] is None
    assert "No deployments in this window yet" in dora.summary(m, "deploy-prod.yml")


def test_a_prod_run_is_measured_from_the_ref_it_released_not_from_main():
    assert dora.released_ref({"display_title": "deploy-prod v0.3.1", "head_sha": "main-head"}) == "v0.3.1"
    assert dora.released_ref({"display_title": "deploy-dev", "head_sha": "abc123"}) == "abc123"
