"""End of an engagement: give the client their data, then erase it, and say exactly what remains.

    python -m frontdesk.admin offboard-export --profile fd-prod --tenant <uuid> --out C:\\handover\\acme --by "Machethe DM"
    python -m frontdesk.admin offboard-erase --profile fd-prod --tenant <uuid> --manifest-sha256 <from the export> --pool <pool id> --by "Machethe DM" --dry-run

The client is the responsible party and owns this data; we are the operator (POPIA s21). So
the export is complete (raw messages as staff saw them, documents, consents, handoffs, the
insights summary), every file is hashed into a manifest, and the export is audited. The erase
refuses to run unless the assistant is already paused (nothing new arriving) and the caller
quotes the manifest hash of a completed export (no erasing before the client has their copy).

What remains after the erase, on purpose and written into the certificate: conversation
events and the audit log (ids, decisions and timings, never text: enforced by a CHECK and a
test), consent records (append-only proof of lawful messaging), the anonymised customer and
staff rows those ids point to, and Aurora's own backups
until they expire (7 days in dev, 14 in prod). The analytics copy in Iceberg also holds no text;
deleting the tenant's rows there is SEAM(offboard-athena), an Athena DELETE run with AWS access.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import uuid

from . import audit, controls, deletion, insights
from .db import Database, Transaction
from .write_guard import WritePlan

FILES = ("conversations.jsonl", "documents.jsonl", "consents.jsonl", "handoffs.jsonl", "insights.json")


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, default=str, ensure_ascii=False) + "\n" for r in rows)


def export(db: Database, tenant_id: uuid.UUID, out: pathlib.Path, by: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with db.transaction(tenant_id) as tx:
        convs = tx.execute(
            """SELECT c.conversation_id::text AS conversation_id, c.channel, c.status, c.created_at,
                      cu.display_name AS customer_name, cu.channel_address AS customer_address,
                      coalesce(json_agg(json_build_object('at', m.created_at, 'direction', m.direction, 'author', m.author, 'text', m.body)
                               ORDER BY m.created_at) FILTER (WHERE m.message_id IS NOT NULL), '[]') AS messages
               FROM app.conversations c JOIN app.customers cu ON cu.tenant_id = c.tenant_id AND cu.customer_id = c.customer_id
               LEFT JOIN app.messages m ON m.tenant_id = c.tenant_id AND m.conversation_id = c.conversation_id
               GROUP BY c.conversation_id, c.channel, c.status, c.created_at, cu.display_name, cu.channel_address
               ORDER BY c.created_at""")
        docs = tx.execute("""SELECT title, source_uri, version, content_sha256, approved, approved_by, approved_at, review_by
                             FROM app.documents ORDER BY source_uri, version""")
        consents = tx.execute("""SELECT customer_id::text AS customer_id, purpose, granted, source, evidence, recorded_at
                                 FROM app.consents ORDER BY recorded_at""")
        handoffs = tx.execute("""SELECT conversation_id::text AS conversation_id, reason, summary, created_at, resolved_at
                                 FROM app.handoffs ORDER BY created_at""")
        summary = insights.summary(tx, dt.datetime.now(dt.UTC) - dt.timedelta(days=366))
        content = {"conversations.jsonl": _jsonl(convs), "documents.jsonl": _jsonl(docs), "consents.jsonl": _jsonl(consents),
                   "handoffs.jsonl": _jsonl(handoffs), "insights.json": json.dumps(summary, default=str, indent=2)}
        hashes = {}
        for name in FILES:
            data = content[name].encode("utf-8")
            (out / name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        manifest = {"tenant_id": str(tenant_id), "exported_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "by": by,
                    "counts": {"conversations": len(convs), "documents": len(docs), "consents": len(consents), "handoffs": len(handoffs)},
                    "sha256": hashes}
        raw = json.dumps(manifest, indent=2).encode()
        (out / "manifest.json").write_bytes(raw)
        manifest_sha = hashlib.sha256(raw).hexdigest()
        audit.record(tx, actor_type="operator", actor_id=by, action="export", resource_type="tenant", resource_id=str(tenant_id),
                     detail={"manifest_sha256": manifest_sha, **manifest["counts"]})
    return {**manifest, "manifest_sha256": manifest_sha}


def _exported(tx: Transaction, manifest_sha: str) -> bool:
    return tx.one("""SELECT 1 AS x FROM app.audit_log WHERE action = 'export' AND resource_type = 'tenant'
                     AND detail->>'manifest_sha256' = :h""", {"h": manifest_sha}) is not None


def erase_plan(tenant_id: uuid.UUID, manifest_sha: str, by: str) -> WritePlan:
    def precondition(tx: Transaction) -> str | None:
        if not _exported(tx, manifest_sha):
            return "no completed export with that manifest hash: export first, and quote the hash it printed"
        if controls.paused(tx) is None:
            return "pause the assistant first (admin pause-assistant), so nothing new arrives during the erase"
        return None

    def apply(tx: Transaction) -> dict:
        customers = [r["customer_id"] for r in tx.execute("SELECT customer_id::text AS customer_id FROM app.customers WHERE deleted_at IS NULL")]
        for c in customers:
            deletion.erase_customer(tx, c, f"offboarding:{by}")
        chunks = tx.execute("DELETE FROM app.chunks RETURNING chunk_id")
        docs = tx.execute("""UPDATE app.documents SET approved = false, title = '[erased at offboarding]',
                                    source_uri = 'erased://' || document_id::text RETURNING document_id""")
        staff = tx.execute("""UPDATE app.staff SET active = false, display_name = '[erased]',
                                     email = 'erased-' || staff_id::text || '@invalid', cognito_sub = NULL RETURNING staff_id""")
        tx.execute("""UPDATE app.tenant_config SET value = '{}'::jsonb, updated_at = now()
                      WHERE key NOT IN ('assistant_mode')""")
        report = {"customers_erased": len(customers), "chunks_deleted": len(chunks), "documents_erased": len(docs), "staff_erased": len(staff)}
        audit.record(tx, actor_type="operator", actor_id=by, action="delete", resource_type="tenant", resource_id=str(tenant_id),
                     detail={"manifest_sha256": manifest_sha, **report})
        return report

    return WritePlan(f"erase tenant {tenant_id} after its export {manifest_sha[:12]}", tenant_id,
                     ["every customer: messages erased, record anonymised, chunks from conversations deleted",
                      "every document chunk and embedding deleted; document rows renamed", "staff deactivated and anonymised",
                      "tenant settings cleared; the assistant stays paused",
                      "kept: events and audit log (no text), Aurora backups until they expire"], precondition, apply)


def certificate(tenant_name: str, export_manifest: dict, report: dict, by: str) -> str:
    when = dt.datetime.now(dt.UTC).strftime("%d %B %Y")
    c = export_manifest["counts"]
    return "\n".join([
        f"# Data return and erasure: {tenant_name}", "",
        f"Date: {when}. Operator: {by} (Front Desk). Responsible party: {tenant_name}.", "",
        "## 1. Returned to you", "",
        f"{c['conversations']} conversations, {c['documents']} document versions, {c['consents']} consent records and "
        f"{c['handoffs']} handoffs, exported on {export_manifest['exported_at']}. Each file's SHA-256 is in `manifest.json`; "
        "the manifest's own hash is recorded in our audit log.", "",
        "## 2. Erased", "",
        f"- Customers: {report['customers_erased']} (messages erased, names and numbers removed)",
        f"- Knowledge chunks and their embeddings: {report['chunks_deleted']}",
        f"- Documents: {report['documents_erased']} (titles and sources removed; no content was stored outside the chunks)",
        f"- Staff accounts: {report['staff_erased']} (deactivated and anonymised; Cognito users removed separately)", "",
        "## 3. What remains, and why", "",
        "- Conversation events and the audit log: ids, decision types and timings only. No message text, names or",
        "  numbers (a database constraint forbids text in events). Kept as the record that the service was run",
        "  correctly and that this erasure happened.",
        "- Consent records (which anonymised customer said yes or no to service or marketing, and when): kept",
        "  as proof that messages were sent lawfully; they point only to the anonymised customer rows.",
        "- Database backups: they expire automatically within 14 days of today and are encrypted.",
        "- Nothing was used to train any model, and nothing was shared with another client.", "",
        "Questions about this certificate: contact the operator named above.",
    ]) + "\n"
