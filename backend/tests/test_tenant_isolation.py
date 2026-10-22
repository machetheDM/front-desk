"""Two tenants, one database: nothing crosses. These tests fail the build if it does."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from frontdesk.db import vector_literal

pytestmark = pytest.mark.db


def _seed_conversation(db, tenant_id, text="Do you install in Seshego?"):
    with db.transaction(tenant_id) as tx:
        cust = tx.scalar(
            "INSERT INTO app.customers (tenant_id, channel, channel_address, display_name) "
            "VALUES (app.current_tenant(), 'web', :addr, 'Lerato') RETURNING customer_id",
            {"addr": f"session-{uuid.uuid4().hex}"},
        )
        conv = tx.scalar(
            "INSERT INTO app.conversations (tenant_id, customer_id, channel) VALUES (app.current_tenant(), :c, 'web') RETURNING conversation_id",
            {"c": str(cust)},
        )
        tx.execute(
            "INSERT INTO app.messages (tenant_id, conversation_id, direction, author, body) VALUES (app.current_tenant(), :c, 'in', 'customer', :b)",
            {"c": str(conv), "b": text},
        )
    return cust, conv


def _seed_chunk(db, tenant_id, text, vec):
    with db.transaction(tenant_id) as tx:
        doc = tx.scalar(
            "INSERT INTO app.documents (tenant_id, title, source_uri, content_sha256, approved) "
            "VALUES (app.current_tenant(), 'Price list', :u, 'x', true) RETURNING document_id",
            {"u": f"s3://docs/{uuid.uuid4().hex}.md"},
        )
        return tx.scalar(
            "INSERT INTO app.chunks (tenant_id, document_id, ordinal, text, embedding, embedder) "
            "VALUES (app.current_tenant(), :d, 0, :t, :e::vector, 'test') RETURNING chunk_id",
            {"d": str(doc), "t": text, "e": vector_literal(vec)},
        )


def test_a_conversation_from_tenant_a_is_invisible_to_tenant_b(db, two_tenants):
    a, b = two_tenants
    _, conv = _seed_conversation(db, a.tenant_id)
    with db.transaction(b.tenant_id) as tx:
        assert tx.execute("SELECT * FROM app.conversations WHERE conversation_id = :c", {"c": str(conv)}) == []
        assert tx.execute("SELECT * FROM app.messages WHERE conversation_id = :c", {"c": str(conv)}) == []
    with db.transaction(a.tenant_id) as tx:
        assert len(tx.execute("SELECT * FROM app.messages WHERE conversation_id = :c", {"c": str(conv)})) == 1


def test_a_document_chunk_from_tenant_a_is_never_retrieved_for_tenant_b(db, two_tenants):
    a, b = two_tenants
    vec = [1.0] + [0.0] * 1023
    _seed_chunk(db, a.tenant_id, "5kW inverter installed: R65 000", vec)
    with db.transaction(b.tenant_id) as tx:
        # Exactly the query vector of A's chunk: the nearest neighbour must still be nothing.
        rows = tx.execute(
            "SELECT chunk_id FROM app.chunks ORDER BY embedding <=> :q::vector LIMIT 5",
            {"q": vector_literal(vec)},
        )
        assert rows == []
        fts = tx.execute("SELECT chunk_id FROM app.chunks WHERE tsv @@ plainto_tsquery('english', 'inverter')")
        assert fts == []
        assert tx.scalar("SELECT count(*) FROM app.documents") == 0


def test_the_retrieval_module_returns_only_the_callers_tenant(db, two_tenants):
    from frontdesk.retrieval.search import hybrid_search
    from frontdesk.retrieval.embedders import HashingEmbedder

    a, b = two_tenants
    emb = HashingEmbedder()
    _seed_chunk(db, a.tenant_id, "Tenant A secret price: inverter R1", emb.embed_one("inverter price"))
    _seed_chunk(db, b.tenant_id, "Tenant B price: inverter R2", emb.embed_one("inverter price"))
    with db.transaction(b.tenant_id) as tx:
        hits = hybrid_search(tx, "inverter price", emb, k=10)
    assert hits, "tenant B should find its own chunk"
    assert all("Tenant B" in h.text for h in hits)


def test_writing_a_row_for_another_tenant_is_rejected_by_the_database(db, two_tenants):
    a, b = two_tenants
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction(b.tenant_id) as tx:
            tx.execute(
                "INSERT INTO app.customers (tenant_id, channel, display_name) VALUES (:t, 'web', 'x')",
                {"t": str(a.tenant_id)},
            )


def test_updating_another_tenants_rows_changes_nothing(db, two_tenants):
    a, b = two_tenants
    _, conv = _seed_conversation(db, a.tenant_id)
    with db.transaction(b.tenant_id) as tx:
        tx.execute("UPDATE app.messages SET body = 'pwned'")
        tx.execute("UPDATE app.conversations SET status = 'resolved'")
    with db.transaction(a.tenant_id) as tx:
        assert tx.scalar("SELECT body FROM app.messages WHERE conversation_id = :c", {"c": str(conv)}) != "pwned"
        assert tx.scalar("SELECT status FROM app.conversations WHERE conversation_id = :c", {"c": str(conv)}) == "open"


def test_without_a_tenant_context_every_table_reads_as_empty(db, two_tenants):
    a, _ = two_tenants
    _seed_conversation(db, a.tenant_id)
    with db.transaction(None) as tx:
        for table in ("tenants", "customers", "conversations", "messages", "documents", "chunks", "audit_log"):
            assert tx.scalar(f"SELECT count(*) FROM app.{table}") == 0, table


def test_the_app_role_cannot_switch_off_row_level_security(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction(None) as tx:
            tx.execute("ALTER TABLE app.messages DISABLE ROW LEVEL SECURITY")


def test_the_app_role_cannot_bypass_rls_with_a_session_setting(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction(None) as tx:
            tx.execute("SET row_security = off; SELECT * FROM app.messages")


def test_a_cross_tenant_foreign_key_cannot_be_formed(db, two_tenants):
    a, b = two_tenants
    cust_a, _ = _seed_conversation(db, a.tenant_id)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.transaction(b.tenant_id) as tx:
            tx.execute(
                "INSERT INTO app.conversations (tenant_id, customer_id, channel) VALUES (app.current_tenant(), :c, 'web')",
                {"c": str(cust_a)},
            )


def test_every_table_in_the_app_schema_has_forced_rls_a_policy_and_tenant_id(admin_dsn):
    from frontdesk.db.lint_migrations import CATALOG_SQL, rls_catalog_violations

    with psycopg.connect(admin_dsn) as c, c.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(CATALOG_SQL)
        rows = cur.fetchall()
    assert len(rows) >= 12
    assert rls_catalog_violations(rows) == []
