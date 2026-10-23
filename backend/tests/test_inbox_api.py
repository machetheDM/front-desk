"""The staff inbox: tenant from the token only, audited reads, owner-only insights, DET-05."""

from __future__ import annotations

import json
import uuid

import pytest

from frontdesk import audit, inbox_api, pipeline, services, telemetry
from frontdesk.tenancy import DEMO_TENANT_ID

pytestmark = pytest.mark.db


def claims(email="naledi@bushveldsolar.example", groups=("owner",), tenant=DEMO_TENANT_ID):
    return {"sub": f"sub-{email}", "email": email, "custom:tenant_id": str(tenant), "cognito:groups": list(groups)}


def call(db, method, path, c, body=None, sender=None):
    r = inbox_api.handle(db, method, path, c, body, sender)
    return r["statusCode"], json.loads(r["body"])


def _handed_over(db):
    rec = pipeline.receive(db, pipeline.Inbound(DEMO_TENANT_ID, "web", f"web:{uuid.uuid4().hex}", "I want to talk to a person", "Mpho"))
    pipeline.answer(db, services.build("offline"), DEMO_TENANT_ID, rec)
    return rec.conversation_id


def test_reading_a_conversation_is_audited_and_answers_who_read_it(demo_db):
    conv = _handed_over(demo_db)
    status, body = call(demo_db, "GET", f"/v1/inbox/conversations/{conv}", claims("pieter@bushveldsolar.example", ("agent",)))
    assert status == 200 and len(body["messages"]) == 2
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        readers = [r["actor_id"] for r in audit.who_read(tx, "conversation", conv)]
    assert len(readers) == 1 and readers[0].startswith("staff:")


def test_the_handoff_queue_shows_summaries_and_names(demo_db):
    _handed_over(demo_db)
    status, body = call(demo_db, "GET", "/v1/inbox/handoffs", claims())
    assert status == 200 and any(h["reason"] == "human_requested" and h["assigned_to"] for h in body)


def test_a_token_for_another_tenant_cannot_see_this_tenants_conversation(demo_db, two_tenants):
    conv = _handed_over(demo_db)
    a, _ = two_tenants
    with demo_db.transaction(a.tenant_id) as tx:
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'Eve', 'eve@a.example', 'owner')")
    status, body = call(demo_db, "GET", f"/v1/inbox/conversations/{conv}", claims("eve@a.example", tenant=a.tenant_id))
    assert body == {"error": "not found"}


def test_someone_who_is_not_active_staff_is_refused_even_with_a_valid_token(demo_db):
    status, body = call(demo_db, "GET", "/v1/inbox/handoffs", claims("stranger@example.com"))
    assert status == 403


def test_insights_are_for_owners_only(demo_db):
    assert call(demo_db, "GET", "/v1/insights/summary", claims("thabo@bushveldsolar.example", ("agent",)))[0] == 403
    status, body = call(demo_db, "GET", "/v1/insights/summary", claims())
    assert status == 200 and "handed_to_staff" in body


def test_a_staff_reply_is_stored_audited_and_handed_to_the_channel(demo_db):
    conv = _handed_over(demo_db)
    sent = []
    status, body = call(demo_db, "POST", f"/v1/inbox/conversations/{conv}/reply", claims(), {"text": "Hi Mpho, Naledi here."},
                        sender=lambda tx, ch, addr, text: sent.append((ch, text)) or "ok")
    assert status == 200 and sent == [("web", "Hi Mpho, Naledi here.")]
    call(demo_db, "POST", f"/v1/inbox/conversations/{conv}/resolve", claims())
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert tx.scalar("SELECT status FROM app.conversations WHERE conversation_id = :c", {"c": conv}) == "resolved"


def test_unusual_read_volume_raises_an_event(demo_db, monkeypatch):
    monkeypatch.setattr(inbox_api, "READ_ALERT_THRESHOLD", 2)
    telemetry.CAPTURED.clear()
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'Bulk Reader', 'bulk@bushveldsolar.example', 'agent') ON CONFLICT DO NOTHING")
    convs = [_handed_over(demo_db) for _ in range(3)]
    for c in convs:
        call(demo_db, "GET", f"/v1/inbox/conversations/{c}", claims("bulk@bushveldsolar.example", ("agent",)))
    assert [e for e in telemetry.CAPTURED if e.get("event") == "UnusualTranscriptReads"]


def test_a_deletion_request_previews_then_erases_text_but_keeps_the_event_stream(demo_db):
    conv = _handed_over(demo_db)
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        cust = tx.scalar("SELECT customer_id::text FROM app.conversations WHERE conversation_id = :c", {"c": conv})
        n_events = tx.scalar("SELECT count(*) FROM app.conversation_events WHERE conversation_id = :c", {"c": conv})
    status, body = call(demo_db, "POST", "/v1/admin/deletion-requests", claims(), {"customer_id": cust})
    assert status == 200 and body["dry_run"] is True
    status, body = call(demo_db, "POST", "/v1/admin/deletion-requests", claims(), {"customer_id": cust, "confirm": True})
    assert status == 200 and body["messages_erased"] == 2
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        bodies = {r["body"] for r in tx.execute("SELECT body FROM app.messages WHERE conversation_id = :c", {"c": conv})}
        assert bodies == {"[deleted on request]"}
        assert tx.scalar("SELECT display_name FROM app.customers WHERE customer_id = :c", {"c": cust}) is None
        assert tx.scalar("SELECT count(*) FROM app.conversation_events WHERE conversation_id = :c", {"c": conv}) == n_events
    assert call(demo_db, "POST", "/v1/admin/deletion-requests", claims(), {"customer_id": cust, "confirm": True})[0] == 409
