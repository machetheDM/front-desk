"""Group customer questions into topics: UMAP down to a few dimensions, then HDBSCAN.

Used for the owner's "what your documents are missing" list: forty unanswered questions are
hard to act on; five topics with a count and an example each are a to-do list.

Why this pair: HDBSCAN finds clusters of any shape and labels outliers as noise instead of
forcing every question into a topic, but density is meaningless in 1024 dimensions. UMAP keeps
each question's nearest neighbours while reducing to five dimensions, where density works.
Topics are named with class-based TF-IDF (the words that are frequent in one cluster and rare
in the others) and shown with the member closest to the cluster centre.

Embeddings: the offline HashingEmbedder (character n-grams) clusters by wording, so "warranty"
and "guarantee" can land apart. With the Bedrock pipeline the same code takes Cohere
multilingual embeddings, which cluster by meaning (SEAM(bedrock-embeddings-clustering)).
"""

from __future__ import annotations

import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import HDBSCAN, KMeans
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

from frontdesk.retrieval.embedders import HashingEmbedder
from frontdesk.retrieval.query_rewrite import rewrite

STOP = set("""a an and are be by can do does for from have how i if in is it its me my of on or our please so that the
this to was we what when where which who will with you your yo ke le go ya na ba e ka mo ga gore ek is die en jy
julle hoe wat vir""".split())


@dataclass
class Topic:
    label: int
    size: int
    share: float
    keywords: list[str]
    example: str
    members: list[str]


def embed(texts: list[str], embedder=None) -> np.ndarray:
    e = embedder or HashingEmbedder()
    X = np.asarray([e.embed_one(rewrite(t).expanded) for t in texts], dtype=np.float32)
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)


def reduce(X: np.ndarray, *, n_components: int = 5, n_neighbors: int = 10, seed: int = 7) -> np.ndarray:
    import umap  # imported here: numba compiles on first import, and only analyses need it

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "n_jobs overridden by random_state": determinism is the point
        return umap.UMAP(n_components=n_components, n_neighbors=min(n_neighbors, len(X) - 1), min_dist=0.0,
                         metric="cosine", random_state=seed).fit_transform(X)


def labels_for(X: np.ndarray, *, min_cluster_size: int = 4, seed: int = 7) -> np.ndarray:
    if len(X) < 2 * min_cluster_size:
        return np.full(len(X), -1)
    Z = reduce(X, seed=seed)
    return HDBSCAN(min_cluster_size=min_cluster_size, min_samples=2).fit_predict(Z)


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9-]+", text.lower()) if w not in STOP and len(w) > 2]


def keywords(groups: dict[int, list[str]], top: int = 4) -> dict[int, list[str]]:
    """Class-based TF-IDF: term frequency within a topic, weighted by how rare the term is
    across topics (log(1 + average words per topic / term's total frequency))."""
    tf = {k: Counter(w for t in v for w in _tokens(t)) for k, v in groups.items()}
    total = Counter()
    for c in tf.values():
        total.update(c)
    avg = sum(sum(c.values()) for c in tf.values()) / max(len(tf), 1)
    out = {}
    for k, c in tf.items():
        n = sum(c.values()) or 1
        scores = {w: (f / n) * math.log(1 + avg / total[w]) for w, f in c.items()}
        out[k] = [w for w, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    return out


def topics(texts: list[str], *, min_cluster_size: int = 3, seed: int = 7, embedder=None) -> tuple[list[Topic], list[str]]:
    """Topics sorted by size, plus the questions HDBSCAN called noise (shown, not hidden)."""
    X = embed(texts, embedder)
    lab = labels_for(X, min_cluster_size=min_cluster_size, seed=seed)
    groups: dict[int, list[int]] = {}
    for i, label in enumerate(lab):
        groups.setdefault(int(label), []).append(i)
    named = keywords({k: [texts[i] for i in v] for k, v in groups.items() if k != -1})
    out = []
    for k, idx in groups.items():
        if k == -1:
            continue
        centre = X[idx].mean(axis=0)
        medoid = idx[int(np.argmax(X[idx] @ centre))]
        out.append(Topic(k, len(idx), len(idx) / len(texts), named[k], texts[medoid], [texts[i] for i in idx]))
    out.sort(key=lambda t: -t.size)
    return out, [texts[i] for i in groups.get(-1, [])]


def evaluate(texts: list[str], gold: list[str], *, min_cluster_size: int = 4, seed: int = 7) -> dict:
    """How well do unsupervised topics recover the labelled intents? Scored on the questions
    HDBSCAN clustered (with the noise share reported next to it) and against k-means with the
    true number of intents, which is told the answer HDBSCAN has to discover."""
    X = embed(texts)
    lab = labels_for(X, min_cluster_size=min_cluster_size, seed=seed)
    keep = lab != -1
    k = len(set(gold))
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(X)
    raw = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=2).fit_predict(X)  # ablation: no UMAP
    raw_keep = raw != -1
    g = np.asarray(gold)
    return {
        "n": len(texts), "intents": k, "clusters_found": int(len(set(lab[keep]))),
        "noise_share": round(float(1 - keep.mean()), 3),
        "umap_hdbscan_ari": round(float(adjusted_rand_score(g[keep], lab[keep])), 3) if keep.any() else 0.0,
        "umap_hdbscan_nmi": round(float(normalized_mutual_info_score(g[keep], lab[keep])), 3) if keep.any() else 0.0,
        "hdbscan_without_umap_noise_share": round(float(1 - raw_keep.mean()), 3),
        "hdbscan_without_umap_ari": round(float(adjusted_rand_score(g[raw_keep], raw[raw_keep])), 3) if raw_keep.any() else 0.0,
        "kmeans_ari": round(float(adjusted_rand_score(g, km)), 3),
        "kmeans_nmi": round(float(normalized_mutual_info_score(g, km)), 3),
    }
