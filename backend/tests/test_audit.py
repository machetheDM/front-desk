"""Reads are audited; the audit log and the event stream cannot be rewritten."""

from __future__ import annotations

import psycopg
import pytest

from frontdesk import audit, events

pytestmark = pytest.mark.db


def _conversation(tx):
    cust = tx.scalar("INSERT INTO app.customers (tenant_id, channel, display_name) VALUES (app.current_tenant(), 'web', 'Thabo') RETURNING customer_id")
    return tx.scalar("INSERT INTO app.conversations (tenant_id, customer_id, channel) VALUES (app.current_tenant(), :c, 'web') RETURNING conversation_id", {"c": str(cust)})


def test_who_read_a_conversation_has_an_answer(db, two_tenants):
    a, _ = two_tenants
    with db.transaction(a.tenant_id) as tx:
        conv = _conversation(tx)
        audit.record_read(tx, actor_id="staff:naledi", resource_type="conversation", resource_id=conv)
        audit.record_read(tx, actor_id="staff:pieter", resource_type="conversation", resource_id=conv)
    with db.transaction(a.tenant_id) as tx:
        readers = [r["actor_id"] for r in audit.who_read(tx, "conversation", conv)]
    assert readers == ["staff:naledi", "staff:pieter"]


def test_the_audit_log_rejects_updates_and_deletes_even_from_its_own_tenant(db, two_tenants):
    a, _ = two_tenants
    with db.transaction(a.tenant_id) as tx:
        audit.record(tx, actor_type="system", actor_id="t", action="write", resource_type="x", resource_id="1")
    for stmt in ("UPDATE app.audit_log SET actor_id = 'someone-else'", "DELETE FROM app.audit_log", "TRUNCATE app.audit_log"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.transaction(a.tenant_id) as tx:
                tx.execute(stmt)


def test_the_audit_log_rejects_tampering_even_by_the_table_owner(admin_dsn, db, two_tenants):
    """The trigger holds even for a role with full privileges, not just the app role."""
    a, _ = two_tenants
    with db.transaction(a.tenant_id) as tx:
        audit.record(tx, actor_type="system", actor_id="t", action="write", resource_type="x", resource_id="1")
    with psycopg.connect(admin_dsn) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            c.execute("DELETE FROM app.audit_log")


def test_conversation_events_are_append_only_and_carry_no_message_text(db, two_tenants):
    a, _ = two_tenants
    with db.transaction(a.tenant_id) as tx:
        conv = _conversation(tx)
        events.emit(tx, conv, "message_received", {"message_id": "m1"})
    with pytest.raises(ValueError):
        with db.transaction(a.tenant_id) as tx:
            events.emit(tx, conv, "answer_sent", {"text": "R65 000"})
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction(a.tenant_id) as tx:
            tx.execute("INSERT INTO app.conversation_events (tenant_id, conversation_id, type, payload) VALUES (app.current_tenant(), :c, 'answer_sent', '{\"body\": \"x\"}')", {"c": str(conv)})
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction(a.tenant_id) as tx:
            tx.execute("DELETE FROM app.conversation_events")


def test_conversation_status_is_derived_from_events(db, two_tenants):
    a, _ = two_tenants
    with db.transaction(a.tenant_id) as tx:
        conv = _conversation(tx)
        events.emit(tx, conv, "message_received")
        events.emit(tx, conv, "handed_over", {"staff_id": "s1"})
        assert tx.scalar("SELECT status FROM app.conversations WHERE conversation_id = :c", {"c": str(conv)}) == "handed_over"
        events.emit(tx, conv, "resolved")
        events.emit(tx, conv, "message_received")
        assert tx.scalar("SELECT status FROM app.conversations WHERE conversation_id = :c", {"c": str(conv)}) == "open"
