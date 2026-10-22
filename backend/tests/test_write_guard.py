"""Writes to a tenant preview first and re-check their precondition at the moment of writing."""

from __future__ import annotations

import uuid

import pytest

from frontdesk.write_guard import PreconditionFailed, WritePlan, execute

pytestmark = pytest.mark.db


def _plan(tenant_id, marker):
    def precondition(tx):
        n = tx.scalar("SELECT count(*) FROM app.staff WHERE email = :e", {"e": marker})
        return "already exists" if n else None

    def apply(tx):
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'X', :e, 'agent')", {"e": marker})
        return {"created": marker}

    return WritePlan("create staff", tenant_id, [marker], precondition, apply)


def test_a_dry_run_writes_nothing(db, two_tenants):
    a, _ = two_tenants
    marker = f"{uuid.uuid4().hex}@example.invalid"
    assert execute(db, _plan(a.tenant_id, marker), write=False, echo=lambda s: None) is None
    with db.transaction(a.tenant_id) as tx:
        assert tx.scalar("SELECT count(*) FROM app.staff WHERE email = :e", {"e": marker}) == 0


def test_the_precondition_is_checked_again_inside_the_writing_transaction(db, two_tenants):
    a, _ = two_tenants
    marker = f"{uuid.uuid4().hex}@example.invalid"
    plan = _plan(a.tenant_id, marker)
    execute(db, plan, write=False, echo=lambda s: None)  # preview: precondition holds
    # Someone else writes between the preview and the write.
    with db.transaction(a.tenant_id) as tx:
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'Y', :e, 'agent')", {"e": marker})
    with pytest.raises(PreconditionFailed, match="already exists"):
        execute(db, plan, write=True, echo=lambda s: None)
    with db.transaction(a.tenant_id) as tx:
        assert tx.scalar("SELECT count(*) FROM app.staff WHERE email = :e", {"e": marker}) == 1
