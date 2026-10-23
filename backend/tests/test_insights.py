"""Dashboard figures are derived from events, never stored as truth."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from frontdesk import insights, pipeline, services
from frontdesk.tenancy import create_tenant

pytestmark = pytest.mark.db


def test_the_owner_summary_is_computed_from_events_and_follows_them(demo_db):
    from frontdesk import demo_tenant

    t = create_tenant(demo_db, "Insights Co", f"ins-{uuid.uuid4().hex[:6]}")
    with demo_db.transaction(t.tenant_id) as tx:
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'Owner', 'o@ins.example', 'owner')")
    deps = services.build("offline")
    _ = demo_tenant
    sunday_night = dt.datetime(2026, 10, 18, 20, 0, tzinfo=dt.UTC)
    for text in ("I want to talk to a person", "Do you install wind turbines?", "Can you do it for R1 000 less?"):
        rec = pipeline.receive(demo_db, pipeline.Inbound(t.tenant_id, "web", f"web:{uuid.uuid4().hex}", text, received_at=sunday_night))
        pipeline.answer(demo_db, deps, t.tenant_id, rec)
    with demo_db.transaction(t.tenant_id) as tx:
        s = insights.summary(tx, since=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
        assert s["messages_received"] == 3 and s["handed_to_staff"] == 3
        assert dict(s["handoff_reasons"]) == {"human_requested": 1, "unanswerable": 1, "negotiation": 1}
        assert s["unanswered_questions"] == ["Do you install wind turbines?"]
        assert 0 < s["handoff_rate"]["ci95"][0] <= s["handoff_rate"]["rate"] <= s["handoff_rate"]["ci95"][1]
        # There is no stored counter to drift: a new event changes the figure immediately.
        conv = tx.scalar("SELECT conversation_id::text FROM app.conversations LIMIT 1")
        tx.execute("INSERT INTO app.conversation_events (tenant_id, conversation_id, type, payload) VALUES (app.current_tenant(), :c, 'message_received', '{\"message_id\": \"x\"}')", {"c": conv})
        assert insights.summary(tx, since=dt.datetime(2026, 10, 1, tzinfo=dt.UTC))["messages_received"] == 4
