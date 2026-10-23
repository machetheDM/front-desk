"""Dashboard figures are derived from events, never stored as truth."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from frontdesk import insights, pipeline, services
from frontdesk.retrieval.ingest import review_by
from frontdesk.tenancy import DEMO_TENANT_ID, create_tenant

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


def test_documents_past_or_near_their_review_date_are_listed_for_the_owner(demo_db):
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        early = insights.documents_due(tx, dt.date(2026, 10, 1))
        soon = insights.documents_due(tx, dt.date(2026, 10, 25))
        late = insights.documents_due(tx, dt.date(2026, 11, 5))
    assert [d["title"] for d in early] == []
    assert soon == [{"title": "Price list", "review_by": "2026-11-01", "overdue": False}]
    assert late[0] == {"title": "Price list", "review_by": "2026-11-01", "overdue": True}


def test_a_document_without_a_review_date_gets_six_months():
    assert review_by({}, today=dt.date(2026, 10, 23)) == dt.date(2027, 4, 23)
    assert review_by({"review_by": "2027-01-31"}) == dt.date(2027, 1, 31)
