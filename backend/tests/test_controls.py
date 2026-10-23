"""The operator's incident switch: pause the assistant for one tenant, without a deploy."""

from __future__ import annotations

import uuid

import pytest

from conftest import ask
from frontdesk import controls, pipeline, services, telemetry
from frontdesk.agent.state_machine import Agent
from frontdesk.tenancy import DEMO_TENANT_ID
from frontdesk.write_guard import PreconditionFailed, execute

pytestmark = pytest.mark.db
quiet = {"echo": lambda s: None}


@pytest.fixture
def paused(demo_db):
    execute(demo_db, controls.pause_plan(DEMO_TENANT_ID, "INC-TEST wrong prices in a document", "Test Operator"), write=True, **quiet)
    yield
    execute(demo_db, controls.resume_plan(DEMO_TENANT_ID, "INC-TEST resolved in the test", "Test Operator"), write=True, **quiet)


def test_while_paused_every_question_goes_to_a_named_person_and_the_customer_is_told(demo_db, paused):
    o = ask(demo_db, Agent(services.build("offline")), "How much is the Home 5kW backup package?")
    assert o.action == "degraded" and o.handoff_reason == "operator_paused"
    assert o.assigned_staff is not None and "A person from Bushveld Solar & Electrical will reply" in o.reply
    assert not o.model_inputs  # nothing reached a model


def test_stop_still_works_while_the_assistant_is_paused(demo_db, paused):
    assert ask(demo_db, Agent(services.build("offline")), "STOP").action == "opted_out"


def test_a_pause_is_audited_with_who_and_why_and_resuming_restores_answers(demo_db):
    execute(demo_db, controls.pause_plan(DEMO_TENANT_ID, "INC-TEST poisoned FAQ found", "Test Operator"), write=True, **quiet)
    with pytest.raises(PreconditionFailed, match="already paused"):
        execute(demo_db, controls.pause_plan(DEMO_TENANT_ID, "INC-TEST second attempt", "Test Operator"), write=True, **quiet)
    execute(demo_db, controls.resume_plan(DEMO_TENANT_ID, "INC-TEST FAQ removed", "Test Operator"), write=True, **quiet)
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        rows = tx.execute("SELECT actor_id, detail FROM app.audit_log WHERE resource_type = 'assistant_mode' ORDER BY at DESC LIMIT 2")
    assert [r["detail"]["mode"] for r in rows] == ["active", "paused"]
    assert rows[1]["detail"]["reason"] == "INC-TEST poisoned FAQ found" and rows[1]["actor_id"] == "Test Operator"
    assert ask(demo_db, Agent(services.build("offline")), "How much is the Home 5kW backup package?").action == "answer"


def test_a_pause_is_a_decision_not_an_outage_so_it_does_not_raise_the_degraded_alarm(demo_db, paused):
    telemetry.CAPTURED.clear()
    rec = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "web", f"web:{uuid.uuid4().hex}", "Do you install in Tzaneen?", None))
    pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    names = {k for e in telemetry.CAPTURED for k in e if k in ("Degraded", "AssistantPaused")}
    assert names == {"AssistantPaused"}


def test_a_pause_needs_a_reason_someone_can_understand_later():
    with pytest.raises(ValueError, match="reason"):
        controls.pause_plan(DEMO_TENANT_ID, "oops", "Test Operator")
