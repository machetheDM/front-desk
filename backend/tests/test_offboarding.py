"""End of an engagement: export everything, then erase, refusing to erase before the export."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from frontdesk import controls, offboarding, pipeline, services
from frontdesk.retrieval.embedders import HashingEmbedder
from frontdesk.retrieval.ingest import plan_ingest, read_docs
from frontdesk.tenancy import create_tenant
from frontdesk.write_guard import PreconditionFailed, execute

pytestmark = pytest.mark.db
quiet = {"echo": lambda s: None}


def _client(db, tmp_path, name):
    t = create_tenant(db, name, f"{name.split()[0].lower()}-{uuid.uuid4().hex[:6]}")
    with db.transaction(t.tenant_id) as tx:
        tx.execute("INSERT INTO app.staff (tenant_id, display_name, email, role) VALUES (app.current_tenant(), 'Owner Person', :e, 'owner')",
                   {"e": f"owner-{uuid.uuid4().hex[:6]}@client.example"})
    doc = tmp_path / f"{t.slug}.md"
    doc.write_text("---\ntitle: Opening hours\n---\n# Opening hours\n\nWe are open Monday to Friday, 08:00 to 17:00.\n")
    execute(db, plan_ingest(t.tenant_id, read_docs([doc]), HashingEmbedder(), approved_by="Owner Person", is_demo_content=False), write=True, **quiet)
    for text in ("When are you open on Friday?", "my number is 082 555 7788, please call me"):
        rec = pipeline.receive(db, pipeline.Inbound(t.tenant_id, "whatsapp", f"+2783{uuid.uuid4().int % 10**7:07d}", text, "Thandi"))
        pipeline.answer(db, services.build("offline"), t.tenant_id, rec)
    return t


def test_offboarding_returns_everything_then_erases_and_leaves_no_text(db, tmp_path):
    t = _client(db, tmp_path, "Leaving Electrical")
    other = _client(db, tmp_path, "Staying Plumbing")

    with pytest.raises(PreconditionFailed, match="export first"):
        execute(db, offboarding.erase_plan(t.tenant_id, "0" * 64, "Test Operator"), write=True, **quiet)

    m = offboarding.export(db, t.tenant_id, tmp_path / "handover", "Test Operator")
    for name, digest in m["sha256"].items():
        assert hashlib.sha256((tmp_path / "handover" / name).read_bytes()).hexdigest() == digest
    assert m["counts"]["conversations"] == 2 and m["counts"]["documents"] == 1
    assert "082 555 7788" in (tmp_path / "handover" / "conversations.jsonl").read_text()  # the client gets the raw record

    with pytest.raises(PreconditionFailed, match="pause the assistant"):
        execute(db, offboarding.erase_plan(t.tenant_id, m["manifest_sha256"], "Test Operator"), write=True, **quiet)
    execute(db, controls.pause_plan(t.tenant_id, "OFFBOARD end of pilot", "Test Operator"), write=True, **quiet)
    report = execute(db, offboarding.erase_plan(t.tenant_id, m["manifest_sha256"], "Test Operator"), write=True, **quiet)
    assert report == {"customers_erased": 2, "chunks_deleted": 1, "documents_erased": 1, "staff_erased": 1}

    with db.transaction(t.tenant_id) as tx:
        bodies = {r["body"] for r in tx.execute("SELECT body FROM app.messages")}
        assert bodies == {"[deleted on request]"}
        assert tx.scalar("SELECT count(*) FROM app.chunks") == 0
        assert tx.scalar("SELECT count(*) FROM app.customers WHERE channel_address IS NOT NULL OR display_name IS NOT NULL") == 0
        assert tx.scalar("SELECT count(*) FROM app.staff WHERE active") == 0
        events = tx.execute("SELECT payload FROM app.conversation_events")
        dumped = json.dumps(events, default=str)
        assert events and "555 7788" not in dumped and "Thandi" not in dumped
    with db.transaction(other.tenant_id) as tx:  # the other client is untouched
        assert tx.scalar("SELECT count(*) FROM app.messages WHERE body LIKE '%555 7788%'") == 1
        assert tx.scalar("SELECT count(*) FROM app.chunks") == 1

    text = offboarding.certificate("Leaving Electrical", m, report, "Test Operator")
    assert "2 conversations" in text and "Customers: 2" in text and "within 14 days" in text
