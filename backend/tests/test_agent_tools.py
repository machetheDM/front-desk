"""Tools are typed, tenant-scoped and cannot commit the business."""

from __future__ import annotations

import pytest

from conftest import ask
from frontdesk.agent import tools
from frontdesk.integrations.orders import ConfigOrdersAdapter
from frontdesk.tenancy import DEMO_TENANT_ID


def test_no_tool_schema_lets_the_model_choose_a_tenant():
    for spec in tools.specs():
        props = spec["toolSpec"]["inputSchema"]["json"].get("properties", {})
        assert not any("tenant" in k for k in props), spec["toolSpec"]["name"]


def test_extra_arguments_from_the_model_are_rejected():
    out = tools.call(tools.ToolContext(tx=None, channel_address=None, orders=None), "get_order_status",
                     {"order_ref": "BSE-1042", "tenant_id": "someone-else"})
    assert out["ok"] is False and "invalid arguments" in out["error"]


def test_order_status_is_only_told_to_the_number_on_the_order():
    a = ConfigOrdersAdapter({"BSE-1042": {"status": "x", "detail": "Installed", "contact": "+27000000042"}})
    assert a.status("BSE-1042", requester_address="+27 00 000 0042") is not None
    assert a.status("BSE-1042", requester_address="+27000000099") is None


def test_a_wrong_number_and_a_missing_order_get_the_same_reply_so_there_is_no_oracle():
    a = ConfigOrdersAdapter({"BSE-1042": {"status": "x", "detail": "Installed", "contact": "+27000000042"}})
    ctx = tools.ToolContext(tx=None, channel_address="+27000000099", orders=a)
    assert tools.call(ctx, "get_order_status", {"order_ref": "BSE-1042"})["result"] == \
        tools.call(ctx, "get_order_status", {"order_ref": "BSE-9999"})["result"]


@pytest.mark.db
def test_asking_for_a_site_assessment_raises_a_callback_request_and_books_nothing(demo_db, offline_agent):
    o = ask(demo_db, offline_agent, "I'd like a site assessment at my house in Flora Park")
    assert o.action == "handoff" and o.handoff_reason == "booking_commitment"
    assert o.requests == [{"kind": "site_assessment", "place": "Flora Park", "preferred_time": ""}]
    assert "can't book" in o.reply


@pytest.mark.db
def test_the_service_area_tool_reads_the_tenants_own_zones(demo_db):
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        out = tools.call(tools.ToolContext(tx, None, None), "check_service_area", {"place": "Mankweng"})
    assert out["ok"] and out["zone"] == "B" and "R850" in out["result"]


@pytest.mark.db
def test_order_status_is_answered_for_the_matching_number(demo_db, offline_agent):
    o = ask(demo_db, offline_agent, "What is the status of my order BSE-1042?", channel_address="+27000000042")
    assert o.action == "answer" and "Installation booked" in o.reply
    o2 = ask(demo_db, offline_agent, "What is the status of my order BSE-1042?", channel_address="+27000000099")
    assert o2.action == "handoff" and o2.handoff_reason == "verification_needed"
    assert "Installation booked" not in o2.reply
