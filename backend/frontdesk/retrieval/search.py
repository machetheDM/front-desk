"""Hybrid search inside the caller's tenant transaction.

Vector search finds paraphrases ("what does it cost" vs "price"); full-text search finds exact
tokens (model numbers, "5kW", suburb names) that embeddings blur. Reciprocal rank fusion merges
the two lists without having to calibrate their scores against each other, then a reranker
reads each candidate with the question and orders the final list.

There is no tenant filter in this SQL on purpose: the transaction's RLS context is the filter.
A test asserts that a chunk from another tenant is never returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..db import Transaction, vector_literal
from .embedders import Embedder
from .query_rewrite import rewrite
from .rerankers import Reranker, tokens

RRF_K = 60


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    title: str
    heading: str
    text: str
    vector_rank: int | None = None
    lexical_rank: int | None = None
    rrf: float = 0.0
    rerank: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        return f"{self.title} > {self.heading}" if self.heading else self.title


VECTOR_SQL = """
SELECT c.chunk_id::text AS chunk_id, c.document_id::text AS document_id, d.title, c.heading, c.text,
       c.embedding <=> :q::vector AS distance
FROM app.chunks c JOIN app.documents d ON d.tenant_id = c.tenant_id AND d.document_id = c.document_id
WHERE d.approved AND NOT c.quarantined AND c.embedder = :embedder AND c.embedding IS NOT NULL
ORDER BY c.embedding <=> :q::vector
LIMIT :n
"""

LEXICAL_SQL = """
SELECT c.chunk_id::text AS chunk_id, c.document_id::text AS document_id, d.title, c.heading, c.text,
       ts_rank_cd(c.tsv, to_tsquery('english', :tsq)) AS score
FROM app.chunks c JOIN app.documents d ON d.tenant_id = c.tenant_id AND d.document_id = c.document_id
WHERE d.approved AND NOT c.quarantined AND c.tsv @@ to_tsquery('english', :tsq)
ORDER BY score DESC
LIMIT :n
"""


def or_tsquery(text: str) -> str:
    # OR semantics: a customer question rarely contains every word of the right chunk.
    words = sorted({w for w in tokens(text) if re.fullmatch(r"[a-z0-9]+", w)})
    return " | ".join(words) if words else ""


def _supports_iterative_scan(tx: Transaction) -> bool:
    v = tx.scalar("SELECT extversion FROM pg_extension WHERE extname = 'vector'") or "0"
    major, minor, *_ = (int(x) for x in (v.split(".") + ["0", "0"])[:3])
    return (major, minor) >= (0, 8)


def hybrid_search(tx: Transaction, query: str, embedder: Embedder, *, k: int = 5,
                  candidates: int = 20, reranker: Reranker | None = None) -> list[Hit]:
    rq = rewrite(query)
    # Iterative HNSW scans keep recall when RLS filters most of the index (pgvector >= 0.8,
    # Aurora). Older pgvector reserves the hnsw.* prefix and would abort the transaction on an
    # unknown setting, so the version is checked first rather than trying and catching.
    if _supports_iterative_scan(tx):
        tx.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")
    qvec = embedder.embed_one(rq.expanded, kind="query")
    vec_rows = tx.execute(VECTOR_SQL, {"q": vector_literal(qvec), "embedder": embedder.name, "n": candidates})
    tsq = or_tsquery(rq.expanded)
    lex_rows = tx.execute(LEXICAL_SQL, {"tsq": tsq, "n": candidates}) if tsq else []

    hits: dict[str, Hit] = {}
    for rank, r in enumerate(vec_rows, 1):
        h = hits.setdefault(r["chunk_id"], Hit(r["chunk_id"], r["document_id"], r["title"], r["heading"], r["text"]))
        h.vector_rank = rank
        h.rrf += 1.0 / (RRF_K + rank)
    for rank, r in enumerate(lex_rows, 1):
        h = hits.setdefault(r["chunk_id"], Hit(r["chunk_id"], r["document_id"], r["title"], r["heading"], r["text"]))
        h.lexical_rank = rank
        h.rrf += 1.0 / (RRF_K + rank)
    fused = sorted(hits.values(), key=lambda h: h.rrf, reverse=True)[:candidates]
    if reranker and fused:
        scores = reranker.scores(rq.expanded, [f"{h.heading}\n{h.text}" for h in fused])
        for h, s in zip(fused, scores):
            h.rerank = s
        fused.sort(key=lambda h: (h.rerank, h.rrf), reverse=True)
    for h in fused:
        h.extra["code_switched"] = rq.code_switched
    return fused[:k]
