# ADR 0006: A deterministic offline pipeline gates every pull request

- Status: accepted
- Date: 2026-10-22

## Context
Model evals that need Bedrock cost money per run and cannot run on a fork's pull request without
credentials. Without a gate on every PR, a change to chunking, retrieval, redaction or the
refusal logic can merge unmeasured.

## Decision
The same pipeline runs with swappable components:

| Component | offline (every PR) | bedrock (release, on demand) |
|---|---|---|
| Embedder | `HashingEmbedder` (char n-gram feature hashing, 1024-d) | Cohere Embed Multilingual |
| Reranker | `LexicalReranker` (BM25-style overlap with field boosts) | Cohere Rerank 3.5 |
| Answerer | `ExtractiveAnswerer` (selects cited sentences, no generation) | Claude Haiku/Sonnet via router |
| Judge | `RubricJudge` (required/forbidden facts) | Claude judge, calibrated with kappa |

Floors for both are in `docs/PLAN.md` section 5. The offline floors are lower because the
components are weaker; they still catch regressions in everything around the model.

## Consequences
Two numbers per metric, clearly labelled. The offline numbers must never be presented as the
product's quality.
