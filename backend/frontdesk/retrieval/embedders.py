"""Embedders. Same interface offline and in production (ADR 0006)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Protocol

import numpy as np

DIM = 1024


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]: ...

    def embed_one(self, text: str, *, kind: str = "query") -> list[float]: ...


_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Character n-gram feature hashing into 1024 dimensions.

    Deterministic, no network, no model download: it lets CI measure retrieval on every pull
    request. It knows nothing about meaning (no synonyms beyond the glossary), so it is the
    floor, not the product. Character n-grams make it tolerant of spelling variation
    ("inverta", "invertor"), which matters for WhatsApp text.
    """

    name = "hashing-v1"

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def _features(self, text: str) -> dict[int, float]:
        feats: dict[int, float] = {}
        tokens = _TOKEN.findall(text.lower())
        for tok in tokens:
            self._add(feats, "w:" + tok, 1.0)
            padded = f"#{tok}#"
            for n in (3, 4):
                for i in range(max(len(padded) - n + 1, 1)):
                    self._add(feats, f"c{n}:" + padded[i : i + n], 0.5)
        for a, b in zip(tokens, tokens[1:]):
            self._add(feats, f"b:{a}_{b}", 0.7)
        return feats

    def _add(self, feats: dict[int, float], key: str, w: float) -> None:
        h = hashlib.blake2b(key.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % self.dim
        sign = 1.0 if h[4] & 1 else -1.0
        feats[idx] = feats.get(idx, 0.0) + sign * w

    def embed_one(self, text: str, *, kind: str = "query") -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        for i, w in self._features(text).items():
            v[i] += w
        n = float(np.linalg.norm(v))
        return (v / n).tolist() if n > 0 else v.tolist()

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        return [self.embed_one(t, kind=kind) for t in texts]


class BedrockCohereEmbedder:
    """Cohere Embed Multilingual v3 on Bedrock (eu-central-1, ADR 0002).

    Multilingual because customers code-switch between Sepedi and English. 1024 dimensions,
    matching the column. `input_type` differs for documents and queries, as Cohere requires.
    """

    name = "cohere-embed-multilingual-v3"
    MODEL_ID = "cohere.embed-multilingual-v3"

    def __init__(self, client=None, region: str = "eu-central-1") -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self.client = client

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 96):  # Cohere's batch limit
            body = {"texts": [t[:2048] for t in texts[i : i + 96]],
                    "input_type": "search_document" if kind == "document" else "search_query",
                    "truncate": "END"}
            resp = self.client.invoke_model(modelId=self.MODEL_ID, body=json.dumps(body))
            out.extend(json.loads(resp["body"].read())["embeddings"])
        return out

    def embed_one(self, text: str, *, kind: str = "query") -> list[float]:
        return self.embed([text], kind=kind)[0]


class BedrockTitanEmbedder:
    """Titan Text Embeddings V2, kept as the comparison arm in the retrieval eval."""

    name = "titan-embed-text-v2"
    MODEL_ID = "amazon.titan-embed-text-v2:0"

    def __init__(self, client=None, region: str = "eu-central-1") -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self.client = client

    def embed_one(self, text: str, *, kind: str = "query") -> list[float]:
        body = {"inputText": text[:8000], "dimensions": DIM, "normalize": True}
        resp = self.client.invoke_model(modelId=self.MODEL_ID, body=json.dumps(body))
        return json.loads(resp["body"].read())["embedding"]

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        return [self.embed_one(t, kind=kind) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return 0.0 if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b)) / (na * nb)


def by_name(name: str) -> Embedder:
    return {"hashing-v1": HashingEmbedder, "cohere-embed-multilingual-v3": BedrockCohereEmbedder,
            "titan-embed-text-v2": BedrockTitanEmbedder}[name]()
