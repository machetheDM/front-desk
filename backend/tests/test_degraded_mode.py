"""If the model provider is down, the business still runs (AGENTS.md)."""

from __future__ import annotations

import pytest

from conftest import ask
from frontdesk import services
from frontdesk.agent.state_machine import Agent
from frontdesk.llm.base import ModelUnavailable

pytestmark = pytest.mark.db


class DownModel:
    name = "down"

    def draft(self, req, *, large=False, run_tool=None):
        raise ModelUnavailable("ThrottlingException: Bedrock is unavailable")

    def summarise(self, lines):
        raise ModelUnavailable("down")


def test_when_bedrock_fails_the_customer_is_told_a_person_will_reply_and_staff_get_it(demo_db):
    deps = services.build("offline")
    deps.model = DownModel()
    o = ask(demo_db, Agent(deps), "How much is the 8kW family system?")
    assert o.action == "degraded"
    assert "A person from Bushveld Solar & Electrical will reply" in o.reply
    assert o.assigned_staff is not None
    assert o.summary  # the template summary works without the model
    assert o.trace[-1] == "degraded"


def test_rules_that_need_no_model_keep_working_while_it_is_down(demo_db):
    deps = services.build("offline")
    deps.model = DownModel()
    o = ask(demo_db, Agent(deps), "I want to talk to a person")
    assert o.action == "handoff" and o.handoff_reason == "human_requested"
