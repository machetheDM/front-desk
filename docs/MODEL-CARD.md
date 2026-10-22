# Model card: Front Desk answer pipeline

Version: answer.v1 prompt, offline-extractive-v1 baseline, Bedrock pipeline configured
(Claude Haiku 5.5 / Sonnet 5.5, Cohere Embed Multilingual v3, Cohere Rerank 3.5, Guardrails),
handoff classifier v1 (see section 6). Date: 22 October 2026.

## 1. What it does

Answers customer questions for one South African SME at a time, only from that company's
approved documents, citing them; hands everything else to a named staff member with a summary.
It is a retrieval-augmented system with a rules layer, an explicit agent state machine, and an
output validator; the language model is one component, not the whole system.

## 2. Intended use and out of scope

- **Intended:** pre-sales and after-sales questions to SMEs (prices, warranty terms, hours,
  service areas, booking process, order status for the asking number) on WhatsApp and web chat.
- **Out of scope (the system hands over):** negotiation, refunds, bookings beyond callback
  requests, complaints, faults, medical/legal/financial advice. Health, legal and financial
  advice businesses are not pilot clients (docs/PLAN.md).
- **Never:** pretending to be human; inventing a price, date or policy; guessing a title or
  pronoun; marketing without opt-in.

## 3. Pipelines

| | offline (CI, every PR) | bedrock (production, release gate) |
|---|---|---|
| Retrieval | pgvector + Postgres FTS, RRF fusion | same |
| Embeddings | HashingEmbedder (char n-grams, 1024-d) | Cohere Embed Multilingual v3 (1024-d), eu-central-1 |
| Rerank | Lexical BM25 + answer-type match | Cohere Rerank 3.5, eu-central-1 |
| Answerer | Extractive: quotes approved chunks, coverage-based answerability | Claude Haiku 5.5 (default), Sonnet 5.5 (routed), structured output via tool |
| Guardrails | none (our own layers only) | Bedrock Guardrails, input and output (grounding 0.75) |
| Validation | same validator | same validator |

## 4. Evaluation data

- `evals/datasets/bushveld.synthetic.yaml`: 110 synthetic questions for the made-up demo tenant
  (English, Sepedi-English code-switched, Afrikaans-English, WhatsApp spelling), each labelled
  with the right decision, gold chunks and required facts. Split dev (36) / test (74).
- `evals/datasets/bushveld-holdout.synthetic.yaml`: 40 questions written after tuning, run once.
- `evals/redteam/cases.yaml`: 40 attack cases mapped to OWASP LLM Top 10 (2025) and MITRE ATLAS.

**Limitations of the data.** All questions are synthetic, written by the developer; real
customer phrasing is messier. Sepedi phrasing has not yet been reviewed by a first-language
speaker. One tenant, one industry. A real client's replay (Phase 6) is the first real test.

## 5. Results

### Offline baseline (measured 22 October 2026, `reports/` artefact of CI run)

| Metric | Test (gated) | Holdout (honest) | Dev (tuned on) |
|---|---|---|---|
| Retrieval recall@5 | 0.96 | 0.88 | 1.00 |
| Retrieval MRR | 0.92 | 0.76 | 0.96 |
| Answer correctness | 0.78 | 0.52 | 0.92 |
| Groundedness of sent answers | 1.00 | 1.00 | 1.00 |
| Correct answer-or-handoff decision | 0.85 | 0.70 | 0.94 |
| Unanswerable handed over | 0.875 | 0.83 | 1.00 |
| PII patterns reaching a model | 0 | 0 | 0 |
| Red-team attack success | 0 of 40 | | |

Read this honestly: retrieval generalises (0.88 recall@5 on new questions); the heuristic
answerability decision does not (0.70). The baseline never invents (groundedness 1.00): when
it is wrong it quotes a real but irrelevant chunk or hands over a question it could have
answered. That is why it is a CI floor, not the product (ADR 0006, ADR 0008).

### Bedrock pipeline

**Not yet measured.** It needs the dev account (docs/DEPLOY.md steps 14 to 17). The release
workflow runs the same evaluation with `--pipeline bedrock` and gates on the Bedrock floors in
docs/PLAN.md section 5. This card is updated with that table when it exists; until then, no
quality claim is made for the production pipeline.

### Judge calibration

The rubric judge (required facts present, forbidden absent) is deterministic. Agreement with a
human is measured with Cohen's kappa once at least 30 answers per pipeline are hand-labelled
(`python -m frontdesk_ml.label`). **Status: 0 human labels; kappa not yet reported.**

## 6. Handoff and intent classifier

See section 7 of this card once Phase 4 lands: trained model vs rules vs LLM on accuracy,
cost and latency.

## 7. Risks and mitigations

| Risk | Mitigation | Evidence |
|---|---|---|
| Invented price or policy | Every fact in an answer must appear in a cited source; no source, no answer | `test_validate.py`, groundedness metric |
| Prompt injection (direct, indirect) | Rules, Guardrails, quarantine of instruction-like document text, output validator | red-team suite, `test_quarantine.py` |
| PII sent abroad | Redaction before any model call; PII metric must be 0 | `test_redaction.py` |
| Cross-tenant leakage | RLS on chunks and embeddings | `test_tenant_isolation.py`, red-team rt21/rt22 |
| Unequal quality across languages | Per-language metrics in every eval report | eval-report.md "By language" |
| Model outage | Degraded mode hands over with a template summary | `test_degraded_mode.py` |

## 8. Ethical considerations

The assistant states it is automated in its first message. It is designed to reduce unanswered
messages, not to replace staff: every handoff names a person. Customers who write in Sepedi
must not get a worse service; per-language results are tracked and a native-speaker review of
the glossary is an open action.
