"""WhatsApp rules (24-hour window, templates, intake end to end) and marketing consent."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from frontdesk import consent, inbox_api, marketing, pipeline, services
from frontdesk.channels.whatsapp import within_service_window
from frontdesk.tenancy import DEMO_TENANT_ID

pytestmark = pytest.mark.db
OWNER = {"sub": "sub-n", "email": "naledi@bushveldsolar.example", "custom:tenant_id": str(DEMO_TENANT_ID), "cognito:groups": ["owner"]}


def _wa(text, address, at=None):
    return pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", address, text, "Karabo", f"wamid.{uuid.uuid4().hex}",
                            at or dt.datetime.now(dt.UTC))


def _addr():
    return f"+2700{uuid.uuid4().int % 10**7:07d}"


def test_the_24_hour_window_is_measured_from_the_customers_last_message():
    now = dt.datetime(2026, 10, 23, 12, tzinfo=dt.UTC)
    assert within_service_window(now - dt.timedelta(hours=23), now)
    assert not within_service_window(now - dt.timedelta(hours=25), now)
    assert not within_service_window(None, now)


def test_free_text_staff_replies_are_refused_outside_the_window_and_the_template_is_offered(demo_db):
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
    rec = pipeline.receive(demo_db, _wa("I want to talk to a person", _addr(), old))
    pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    r = inbox_api.handle(demo_db, "POST", f"/v1/inbox/conversations/{rec.conversation_id}/reply", OWNER, {"text": "Hi"}, sender=lambda *a: "x")
    assert json.loads(r["body"])["error"] == "outside_24h_window"
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        tx.execute("""INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), 'whatsapp', :v::jsonb, 't')
                      ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value""",
                   {"v": {"phone_number_id": "phone-number-id-demo", "follow_up_template": "bushveld_follow_up_v1"}})
    sent = []
    r = inbox_api.handle(demo_db, "POST", f"/v1/inbox/conversations/{rec.conversation_id}/template", OWNER, {},
                         sender=lambda tx, ch, addr, t: sent.append((ch, t)) or "m1")
    assert json.loads(r["body"])["template"] == "bushveld_follow_up_v1" and sent == [("whatsapp-template", "bushveld_follow_up_v1")]


def test_being_answered_is_not_consent_to_marketing(demo_db):
    rec = pipeline.receive(demo_db, _wa("How much is the 3kW backup?", _addr()))
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert consent.current(tx, rec.customer_id, "service") is True
        assert rec.customer_id not in {a["customer_id"] for a in marketing.audience(tx)}


def test_only_an_explicit_keyword_opts_in_and_stop_takes_it_back(demo_db):
    addr = _addr()
    for maybe in ("yes", "ok thanks", "subscribe"):
        pipeline.receive(demo_db, _wa(maybe, addr))
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        cid = tx.scalar("SELECT customer_id::text FROM app.customers WHERE channel_address = :a", {"a": addr})
        assert cid not in {a["customer_id"] for a in marketing.audience(tx)}
    rec = pipeline.receive(demo_db, _wa("JOIN UPDATES", addr))
    o = pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    assert o.action == "opted_in" and "Send STOP" in o.reply
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert cid in {a["customer_id"] for a in marketing.audience(tx)}
    pipeline.receive(demo_db, _wa("STOP", addr))
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert cid not in {a["customer_id"] for a in marketing.audience(tx)}


def test_the_intake_lambda_verifies_routes_stores_and_starts_the_pipeline_with_ids_only(demo_db, app_dsn, monkeypatch):
    from frontdesk import routing
    from frontdesk.channels import sns_verify
    from frontdesk.handlers import common, whatsapp_intake

    monkeypatch.setenv("FRONTDESK_ROUTING_JSON", json.dumps({"routing/whatsapp/phone-number-id-demo": {"tenant_id": str(DEMO_TENANT_ID)}}))
    monkeypatch.setenv("FRONTDESK_DSN", app_dsn)
    monkeypatch.setenv("FRONTDESK_STATE_MACHINE_ARN", "arn:aws:states:af-south-1:1:stateMachine:x")
    routing._local.cache_clear(); common.db.cache_clear()
    monkeypatch.setattr(sns_verify, "verify", lambda msg, **kw: None)  # signature tests live in test_channels.py
    started = []

    class FakeSfn:
        def start_execution(self, **kw):
            started.append(json.loads(kw["input"]))

    import boto3
    monkeypatch.setattr(boto3, "client", lambda name, **kw: FakeSfn())
    entry = {"changes": [{"value": {"contacts": [{"wa_id": "27000123456", "profile": {"name": "Karabo"}}],
                                    "messages": [{"from": "27000123456", "id": f"wamid.{uuid.uuid4().hex}", "timestamp": "1760000000",
                                                  "type": "text", "text": {"body": "My ID is 8501015800088, how much is the 3kW?"}}]}}]}
    msg = {"context": {"MetaPhoneNumberIds": [{"arn": "arn:aws:social-messaging:af-south-1:1:phone-number-id/phone-number-id-demo"}]},
           "whatsAppWebhookEntry": json.dumps(entry)}
    out = whatsapp_intake.handler({"Records": [{"Sns": {"Message": json.dumps(msg)}}]}, None)
    assert out == {"started": 1}
    payload = json.dumps(started[0])
    assert "8501015800088" not in payload and "how much" not in payload  # ids only
    assert started[0]["phone_number_id"] == "phone-number-id-demo"
    routing._local.cache_clear(); common.db.cache_clear()
