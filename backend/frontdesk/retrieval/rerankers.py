"""Rerankers: reorder the fused candidate list with a model that reads query and chunk together."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

from .query_rewrite import normalise_units

_TOK = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOP = set("a an and are at be by can do does for from have how i if in is it me my of on or our please the this to we what when where which who will with you your ke le ya go".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOK.findall(normalise_units(text).lower()) if t not in STOP]


class Reranker(Protocol):
    name: str

    def scores(self, query: str, docs: list[str]) -> list[float]: ...


class LexicalReranker:
    """BM25 over the candidate set, with a bonus for exact numbers and units.

    "5kw" vs "8kw" is the difference between a right and a wrong quote; generic
    embeddings treat them as near-identical, so exact numeric matches are rewarded and
    mismatched power ratings penalised.
    """

    name = "lexical-bm25-v1"

    def __init__(self, k1: float = 1.2, b: float = 0.6) -> None:
        self.k1, self.b = k1, b

    def scores(self, query: str, docs: list[str]) -> list[float]:
        q = tokens(query)
        dtoks = [tokens(d) for d in docs]
        n = len(docs) or 1
        avg = sum(len(d) for d in dtoks) / n or 1.0
        df = Counter(t for d in dtoks for t in set(d))
        q_units = {t for t in q if re.fullmatch(r"\d+(\.\d+)?(kw|kwh|kva|w)", t)}
        out = []
        for d in dtoks:
            tf = Counter(d)
            s = 0.0
            for t in set(q):
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / avg))
            d_units = {t for t in d if re.fullmatch(r"\d+(\.\d+)?(kw|kwh|kva|w)", t)}
            if q_units:
                s += 2.0 * len(q_units & d_units)
                if d_units and not (q_units & d_units):
                    s -= 1.5
            out.append(s)
        return out


class BedrockCohereReranker:
    """Cohere Rerank 3.5 through the Bedrock Rerank API (eu-central-1).

    One search unit covers a query with up to 100 documents, so the 20 fused candidates cost
    a single unit (USD 0.002, docs/ARCHITECTURE.md cost table).
    """

    name = "cohere-rerank-v3-5"
    MODEL_ARN = "arn:aws:bedrock:eu-central-1::foundation-model/cohere.rerank-v3-5:0"

    def __init__(self, client=None, region: str = "eu-central-1") -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-agent-runtime", region_name=region)
        self.client = client

    def scores(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        resp = self.client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[{"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": d}}} for d in docs],
            rerankingConfiguration={"type": "BEDROCK_RERANKING_MODEL", "bedrockRerankingConfiguration": {
                "numberOfResults": len(docs), "modelConfiguration": {"modelArn": self.MODEL_ARN}}},
        )
        out = [0.0] * len(docs)
        for r in resp["results"]:
            out[r["index"]] = float(r["relevanceScore"])
        return out


def by_name(name: str) -> Reranker:
    return {"lexical-bm25-v1": LexicalReranker, "cohere-rerank-v3-5": BedrockCohereReranker}[name]()

