"""Inbound to persisted outcome: messages, consent, events, handoffs, duplicates, opt-out."""

from __future__ import annotations

import uuid

import pytest

from frontdesk import pipeline, services
from frontdesk.tenancy import DEMO_TENANT_ID

pytestmark = pytest.mark.db


def inbound(text, address=None, msg_id=None):
    return pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", address or f"+2700{uuid.uuid4().int % 10**7:07d}", text,
                            "Lerato", msg_id)


def test_an_answered_message_leaves_both_messages_and_the_events_that_prove_it(demo_db):
    rec = pipeline.receive(demo_db, inbound("How much is the Home 5kW backup package?"))
    assert rec.proceed and rec.first_message
    o = pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    assert o.action == "answer"
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        types = [e["type"] for e in tx.execute("SELECT type FROM app.conversation_events WHERE conversation_id = :c ORDER BY event_id",
                                               {"c": rec.conversation_id})]
        assert types == ["message_received", "answer_sent"]
        msgs = tx.execute("SELECT direction, author FROM app.messages WHERE conversation_id = :c ORDER BY created_at", {"c": rec.conversation_id})
        assert [(m["direction"], m["author"]) for m in msgs] == [("in", "customer"), ("out", "assistant")]
        assert tx.scalar("SELECT granted FROM app.consents WHERE customer_id = :c AND purpose = 'service'", {"c": rec.customer_id}) is True
        assert tx.scalar("SELECT count(*) FROM app.consents WHERE customer_id = :c AND purpose = 'marketing'", {"c": rec.customer_id}) == 0


def test_the_raw_message_is_kept_for_staff_and_the_redacted_one_for_the_model(demo_db):
    rec = pipeline.receive(demo_db, inbound("my ID is 8501015800088, how much is the 3kW?"))
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        m = tx.one("SELECT body, body_redacted, redactions FROM app.messages WHERE message_id = :m", {"m": rec.message_id})
    assert "8501015800088" in m["body"] and "8501015800088" not in m["body_redacted"]
    assert m["redactions"][0]["kind"] == "ID_NUMBER"


def test_a_handoff_creates_a_named_assignment_with_a_summary(demo_db):
    rec = pipeline.receive(demo_db, inbound("Can you do the 5kW for R20 000 less?"))
    pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        h = tx.one("""SELECT h.reason, h.summary, s.display_name FROM app.handoffs h JOIN app.staff s
                      ON s.tenant_id = h.tenant_id AND s.staff_id = h.assigned_staff_id WHERE h.conversation_id = :c""", {"c": rec.conversation_id})
        assert tx.scalar("SELECT status FROM app.conversations WHERE conversation_id = :c", {"c": rec.conversation_id}) == "handed_over"
    assert h["reason"] == "negotiation" and h["display_name"] == "Naledi Mokoena"
    assert "Lerato wrote" in h["summary"] and "Needs: asked for a discount" in h["summary"]


def test_a_whatsapp_retry_with_the_same_message_id_is_not_answered_twice(demo_db):
    mid = f"wamid.{uuid.uuid4().hex}"
    first = pipeline.receive(demo_db, inbound("Hi", "+27000001234", mid))
    again = pipeline.receive(demo_db, inbound("Hi", "+27000001234", mid))
    assert first.proceed and again.duplicate and not again.proceed and again.message_id == first.message_id


def test_after_stop_the_customer_is_not_answered_again(demo_db):
    addr = f"+2700{uuid.uuid4().int % 10**7:07d}"
    rec = pipeline.receive(demo_db, inbound("STOP", addr))
    o = pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    assert o.action == "opted_out"
    later = pipeline.receive(demo_db, inbound("How much is the 3kW?", addr))
    assert not later.proceed and later.reason == "opted_out_earlier"


def test_a_sender_flooding_the_number_is_rate_limited(demo_db):
    addr = f"+2700{uuid.uuid4().int % 10**7:07d}"
    results = [pipeline.receive(demo_db, inbound(f"message {i}", addr)) for i in range(10)]
    assert [r.proceed for r in results][:8] == [True] * 8
    assert results[-1].reason == "rate_limited"


def test_degrade_hands_over_with_a_person_named_even_without_the_model(demo_db):
    rec = pipeline.receive(demo_db, inbound("How much is the 8kW?"))
    o = pipeline.degrade(demo_db, DEMO_TENANT_ID, rec, "States.Timeout")
    assert o.action == "degraded" and "A person from Bushveld" in o.reply
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert tx.scalar("SELECT count(*) FROM app.handoffs WHERE conversation_id = :c", {"c": rec.conversation_id}) == 1
