"""Load a tenant's documents: parse, chunk, embed, store. Built as a WritePlan (preview first)."""

from __future__ import annotations

import pathlib
import uuid
from dataclasses import dataclass

from ..db import Transaction, vector_literal
from ..tenancy import DEMO_TENANT_ID, assert_demo_content_allowed
from ..write_guard import WritePlan
from .chunking import ParsedDoc, chunk_markdown, embedding_text
from .embedders import Embedder
from .quarantine import scan


@dataclass
class SourceDoc:
    slug: str
    uri: str
    parsed: ParsedDoc


def read_docs(paths: list[pathlib.Path], uri_prefix: str = "file://") -> list[SourceDoc]:
    out = []
    for p in sorted(paths):
        parsed = chunk_markdown(p.read_text(encoding="utf-8"), default_title=p.stem)
        out.append(SourceDoc(p.stem, f"{uri_prefix}{p.name}", parsed))
    return out


def plan_ingest(tenant_id: uuid.UUID, docs: list[SourceDoc], embedder: Embedder, *,
                approved_by: str | None, is_demo_content: bool) -> WritePlan:
    assert_demo_content_allowed(tenant_id, is_demo_content)
    items = []
    for d in docs:
        flagged = [c.ordinal for c in d.parsed.chunks if scan(f"{c.heading} {c.text}")]
        items.append(f"{d.uri}: '{d.parsed.title}', {len(d.parsed.chunks)} chunks, sha256 {d.parsed.sha256[:12]}"
                     + ("" if approved_by else " (NOT approved: will not be retrievable)")
                     + (f" QUARANTINED chunks {flagged}: review before they can be retrieved" if flagged else ""))

    def precondition(tx: Transaction) -> str | None:
        row = tx.one("SELECT tenant_id, is_demo FROM app.tenants")
        if row is None:
            return "tenant does not exist (or is not visible in this context)"
        if bool(row["is_demo"]) != (uuid.UUID(str(tenant_id)) == DEMO_TENANT_ID):
            return "tenant demo flag does not match its id"
        if is_demo_content and not row["is_demo"]:
            return "demo content may only be written to the demo tenant"
        for d in docs:
            same = tx.one("SELECT version FROM app.documents WHERE source_uri = :u AND content_sha256 = :h",
                          {"u": d.uri, "h": d.parsed.sha256})
            if same:
                return f"{d.uri} with this exact content is already loaded (version {same['version']})"
        return None

    def apply(tx: Transaction) -> dict:
        n_chunks = quarantined = 0
        for d in docs:
            version = (tx.scalar("SELECT max(version) FROM app.documents WHERE source_uri = :u", {"u": d.uri}) or 0) + 1
            if version > 1:
                # A new version replaces the old one for retrieval; the old row stays for audit.
                tx.execute("UPDATE app.documents SET approved = false WHERE source_uri = :u", {"u": d.uri})
            doc_id = tx.scalar(
                """INSERT INTO app.documents (tenant_id, title, source_uri, content_sha256, version, approved, approved_by, approved_at)
                   VALUES (app.current_tenant(), :t, :u, :h, :v, :a, :by, CASE WHEN :a THEN now() END) RETURNING document_id""",
                {"t": d.parsed.title, "u": d.uri, "h": d.parsed.sha256, "v": version,
                 "a": approved_by is not None, "by": approved_by},
            )
            vectors = embedder.embed([embedding_text(d.parsed.title, c) for c in d.parsed.chunks], kind="document")
            for c, v in zip(d.parsed.chunks, vectors):
                q = scan(f"{c.heading} {c.text}")
                tx.execute(
                    """INSERT INTO app.chunks (tenant_id, document_id, ordinal, heading, text, embedding, embedder, quarantined, quarantine_reason)
                       VALUES (app.current_tenant(), :d, :o, :h, :t, :e::vector, :m, :q, :qr)""",
                    {"d": str(doc_id), "o": c.ordinal, "h": c.heading, "t": c.text, "e": vector_literal(v), "m": embedder.name,
                     "q": q is not None, "qr": q},
                )
                n_chunks += 1
                quarantined += q is not None
        return {"documents": len(docs), "chunks": n_chunks, "quarantined": quarantined, "embedder": embedder.name}

    return WritePlan(f"load {len(docs)} document(s) with {embedder.name}", uuid.UUID(str(tenant_id)), items, precondition, apply)
