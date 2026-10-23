"""Containment and scoping for a stolen staff session (docs/drills/2026-10-23-tabletop-stolen-session.md)."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from frontdesk import audit, inbox_api, pipeline, services, staff
from frontdesk.tenancy import DEMO_TENANT_ID
from frontdesk.write_guard import execute

pytestmark = pytest.mark.db


def _claims(email):
    return {"sub": f"sub-{email}", "email": email, "custom:tenant_id": str(DEMO_TENANT_ID), "cognito:groups": ["agent"]}


def _new_staff(db) -> str:
    email = f"temp-{uuid.uuid4().hex[:6]}@bushveldsolar.example"
    with db.transaction(DEMO_TENANT_ID) as tx:
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'Temp Agent', :e, 'agent')", {"e": email})
    return email


def test_cutting_off_a_staff_member_refuses_their_next_request_even_with_a_valid_token(demo_db):
    email = _new_staff(demo_db)
    assert inbox_api.handle(demo_db, "GET", "/v1/inbox/handoffs", _claims(email))["statusCode"] == 200
    signed_out = []
    execute(demo_db, staff.deactivate_plan(DEMO_TENANT_ID, email, "INC-TEST phished session", "Test Operator", signed_out.append),
            write=True, echo=lambda s: None)
    r = inbox_api.handle(demo_db, "GET", "/v1/inbox/handoffs", _claims(email))  # the same, still-unexpired token
    assert r["statusCode"] == 403 and signed_out == [email]
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        row = tx.one("SELECT actor_id, detail FROM app.audit_log WHERE resource_type = 'staff_access' ORDER BY at DESC LIMIT 1")
    assert row["actor_id"] == "Test Operator" and row["detail"]["reason"] == "INC-TEST phished session"


def test_what_an_account_read_can_be_listed_for_the_incident_window(demo_db):
    email = _new_staff(demo_db)
    start = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    convs = []
    for _ in range(3):
        rec = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "web", f"web:{uuid.uuid4().hex}", "I want to talk to a person", "Mpho"))
        pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
        convs.append(rec.conversation_id)
        inbox_api.handle(demo_db, "GET", f"/v1/inbox/conversations/{rec.conversation_id}", _claims(email))
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        sid = tx.scalar("SELECT staff_id::text FROM app.staff WHERE email = :e", {"e": email})
        reads = audit.reads_by(tx, f"staff:{sid}", start, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1))
    assert [r["resource_id"] for r in reads if r["resource_type"] == "conversation"] == convs
    assert json.dumps(reads, default=str).count("conversation") == 3
