"""Prompt A/B tests: sticky assignment, safe configuration, outcomes from events."""

from __future__ import annotations

import difflib
import pathlib
import uuid

import pytest

from frontdesk import experiments, pipeline, services
from frontdesk.llm.prompts import CURRENT, load
from frontdesk.tenancy import DEMO_TENANT_ID
from frontdesk.write_guard import PreconditionFailed, execute

ROOT = pathlib.Path(__file__).resolve().parents[2]
CFG = {"id": "exp-2026-11-unit-test", "arms": {"control": "answer.v1", "treatment": "answer.v2"}, "allocation": 0.5,
       "status": "running", "prereg_sha256": "0" * 64}


def test_assignment_is_sticky_per_customer_and_close_to_the_allocation():
    arms = [experiments.assign(CFG, f"+2782{i:07d}") for i in range(10_000)]
    assert 0.48 < arms.count("treatment") / len(arms) < 0.52
    assert all(experiments.assign(CFG, "+27820000001") == arms[1] for _ in range(5))
    other = {**CFG, "id": "exp-2026-11-another-test"}
    moved = sum(experiments.assign(other, f"+2782{i:07d}") != a for i, a in enumerate(arms[:2000]))
    assert 800 < moved < 1200  # a new experiment reshuffles customers instead of reusing the old split


@pytest.mark.parametrize("arms, why", [
    ({"control": "answer.v1", "treatment": "../../etc/passwd"}, "must be named answer.vN"),
    ({"control": "answer.v1", "treatment": "answer.v99"}, "does not exist"),
    ({"control": "answer.v1", "treatment": "answer.v1"}, "same prompt"),
    ({"control": "answer.v1"}, "exactly control and treatment"),
])
def test_an_unsafe_or_broken_experiment_is_never_run(arms, why):
    cfg = {**CFG, "arms": arms}
    assert any(why in p for p in experiments.problems(cfg))


def test_the_candidate_prompt_differs_from_the_current_one_only_in_rule_7():
    # One variable per experiment; otherwise a result cannot be attributed to anything.
    v1, v2 = load("answer.v1").text.splitlines(), load("answer.v2").text.splitlines()
    changed = [line for line in difflib.unified_diff(v1, v2, lineterm="", n=0) if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert changed and all("sentence" in line or "Plain text" in line for line in changed)
    assert CURRENT == "answer.v1"


def _prereg(tmp_path, status="approved") -> pathlib.Path:
    src = (ROOT / "experiments" / "exp-2026-11-lead-with-answer.yaml").read_text()
    p = tmp_path / "exp-2026-11-lead-with-answer.yaml"
    p.write_text(src.replace("status: draft", f"status: {status}"))
    return p


@pytest.fixture
def running(demo_db, tmp_path):
    execute(demo_db, experiments.start_plan(DEMO_TENANT_ID, _prereg(tmp_path), "test owner"), write=True, echo=lambda s: None)
    yield "exp-2026-11-lead-with-answer"
    execute(demo_db, experiments.stop_plan(DEMO_TENANT_ID, "exp-2026-11-lead-with-answer", "test finished"), write=True, echo=lambda s: None)


@pytest.mark.db
def test_starting_refuses_a_draft_and_a_file_changed_after_the_preview(demo_db, tmp_path):
    with pytest.raises(PreconditionFailed, match="not 'approved'"):
        execute(demo_db, experiments.start_plan(DEMO_TENANT_ID, _prereg(tmp_path, "draft"), "x"), write=True, echo=lambda s: None)
    path = _prereg(tmp_path)
    plan = experiments.start_plan(DEMO_TENANT_ID, path, "x")
    path.write_text(path.read_text().replace("minimum_detectable_effect: 0.05", "minimum_detectable_effect: 0.10"))
    with pytest.raises(PreconditionFailed, match="changed since the preview"):
        execute(demo_db, plan, write=True, echo=lambda s: None)


@pytest.mark.db
def test_a_running_experiment_records_the_arm_and_its_prompt_on_every_answer(demo_db, running):
    seen = {}
    for i in range(12):
        number = f"+2783{uuid.uuid4().int % 10**7:07d}"
        rec = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", number, "How much is the Home 5kW backup package?", "Test"))
        o = pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
        assert o.experiment["id"] == running
        assert o.prompt_version.startswith({"control": "answer.v1@", "treatment": "answer.v2@"}[o.experiment["arm"]])
        seen[number] = o.experiment["arm"]
        again = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", number, "And the 3kW?", "Test"))
        assert pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, again).experiment["arm"] == seen[number]
    assert set(seen.values()) == {"control", "treatment"}


@pytest.mark.db
def test_outcomes_are_counted_from_events_and_a_later_handoff_counts_against_the_arm(demo_db, running):
    before = _counts(demo_db, running)
    number = f"+2784{uuid.uuid4().int % 10**7:07d}"
    rec = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", number, "How much is the Home 5kW backup package?", "Test"))
    arm = pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec).experiment["arm"]
    after_answer = _counts(demo_db, running)
    assert after_answer[arm]["exposed"] == before[arm]["exposed"] + 1
    assert after_answer[arm]["resolved_without_staff"] == before[arm]["resolved_without_staff"] + 1
    rec2 = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", number, "I want to talk to a person", "Test"))
    pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec2)
    final = _counts(demo_db, running)
    assert final[arm]["resolved_without_staff"] == before[arm]["resolved_without_staff"]
    assert final[arm]["asked_for_person"] == before[arm]["asked_for_person"] + 1


@pytest.mark.db
def test_stopping_an_experiment_returns_everyone_to_the_current_prompt(demo_db, tmp_path):
    execute(demo_db, experiments.start_plan(DEMO_TENANT_ID, _prereg(tmp_path), "x"), write=True, echo=lambda s: None)
    execute(demo_db, experiments.stop_plan(DEMO_TENANT_ID, "exp-2026-11-lead-with-answer", "test"), write=True, echo=lambda s: None)
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert experiments.choose(tx, "+27820000001") == (CURRENT, None)


def _counts(db, exp_id):
    with db.transaction(DEMO_TENANT_ID) as tx:
        return experiments.counts(tx, exp_id)
