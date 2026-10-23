# Model card: Front Desk answer pipeline

Version: answer.v1 prompt, offline-extractive-v1 baseline, Bedrock pipeline configured
(Claude Haiku 5.5 / Sonnet 5.5, Cohere Embed Multilingual v3, Cohere Rerank 3.5, Guardrails),
handoff classifier v1 in shadow mode (section 6). Date: 23 October 2026.

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
- `evals/redteam/cases.yaml`: 42 attack cases mapped to OWASP LLM Top 10 (2025) and MITRE ATLAS
  (control mapping in `docs/AI-SECURITY.md`).
- `evals/datasets/train.synthetic.jsonl`: 380 template-generated rows for the classifier only,
  disjoint from both evaluation sets (a test fails the build on any overlap).

**Limitations of the data.** All questions are synthetic, written by the developer; real
customer phrasing is messier. Sepedi phrasing has not yet been reviewed by a first-language
speaker. One tenant, one industry. A real client's replay (Phase 6) is the first real test.

## 5. Results

### Offline baseline (measured 23 October 2026, `docs/evidence/eval-offline-2026-10-23.md`)

| Metric | Test (gated) | Holdout (honest) | Dev (tuned on) |
|---|---|---|---|
| Retrieval recall@5 | 0.98 | 0.88 | 1.00 |
| Retrieval MRR | 0.93 | 0.76 | 0.96 |
| Answer correctness | 0.80 | 0.52 | 0.92 |
| Groundedness of sent answers | 1.00 | 1.00 | 1.00 |
| Correct answer-or-handoff decision | 0.86 | 0.70 | 0.94 |
| Unanswerable handed over | 0.875 | 0.83 | 1.00 |
| PII patterns reaching a model | 0 | 0 | 0 |
| Red-team attack success | 0 of 42 | | |

Change since 22 October (`eval-offline-2026-10-22.md`): one glossary entry, "office hours" ->
"opening hours", added because the demo replay handed "what are your office hours" over three
times although the documents answer it. One test question uses the same wording, which is the
whole test-split gain (recall@5 0.96 -> 0.98, correctness 0.78 -> 0.80). The holdout did not
move, which is the number to quote.

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

**Question.** Is a trained text classifier better than the hand-written rules
(`agent/policy.py`) at spotting messages that need a person, and at labelling intent? The
LLM arm (`ml/frontdesk_ml/llm_classifier.py`, Claude Haiku via Bedrock) is built and needs the
dev account to run (`python -m frontdesk_ml.classifier compare --llm`).

**Model.** Three logistic regressions (needs-person, reason, intent) on 1024-d hashed
character n-grams of the glossary-expanded text. Trained with scikit-learn, exported as plain
weights (`backend/frontdesk/data/classifier.v1.json`, 0.2 MB) and served with numpy in the
Lambda: no scikit-learn in the package, about 0.2 ms per message, no per-call cost. A test
checks the numpy serving code reproduces scikit-learn's probabilities, and another rebuilds the
committed weights from the committed data.

**Training data.** 380 synthetic rows from templates (`ml/frontdesk_ml/training_data.py`,
seeded). The first draft shared twelve sentences with the evaluation sets; the leakage test
caught it and the templates were rewritten. The numbers below are after the fix; before it,
the model looked better than it was (test F1 0.42 against 0.12 now).

**Results** (full tables: `docs/evidence/classifier-comparison.md`; threshold 0.95 chosen on dev
as the lowest that adds no false handoff over the rules alone):

| Split | System | Needs-person F1 | Precision | Recall | Intent accuracy |
|---|---|---|---|---|---|
| test (74) | rules | 0.97 | 1.00 | 0.93 | 0.84 |
| test (74) | model | 0.12 | 1.00 | 0.07 | 0.74 |
| holdout (40) | rules | 0.80 | 1.00 | 0.67 | 0.68 |
| holdout (40) | model | 0.00 | 0.00 | 0.00 | 0.70 |

The model ranks well without a threshold (ROC AUC 0.98 test, 0.96 holdout), and the decisive
analysis is on the only cases where it could add anything, the handoffs the rules miss. On the
holdout the rules missed three (a bulk discount, an angry complaint, a Sepedi request for a named
person); the model scored them 0.93, 0.91 and 0.73, at or above every true non-handoff (highest
0.73). But the dev split contains no rule misses at all, so no threshold chosen honestly on dev
can claim that benefit, and choosing it on the holdout would be tuning on the test.

**Decision: shadow mode.** The rules act. The model's prediction is stored in every event
payload (`shadow`: probability, would-hand-over, reason, intent, version), computed on
redacted text only, never shown to the customer, and a failure in it is recorded and ignored
(`backend/tests/test_classifier.py`). What would change the decision: a model retrained on a
client's consented, redacted messages, with a dev split that contains real rule misses
(conversations staff took over after an answer), beating the rules on that client's holdout at
no added false handoffs. Retraining never mixes tenants (refusal 6).

## 7. Risks and mitigations

| Risk | Mitigation | Evidence |
|---|---|---|
| Invented price or policy | Every fact in an answer must appear in a cited source; no source, no answer | `test_validate.py`, groundedness metric |
| Prompt injection (direct, indirect) | Rules, Guardrails, quarantine of instruction-like document text, output validator | red-team suite, `test_quarantine.py` |
| PII sent abroad | Redaction before any model call; PII metric must be 0 | `test_redaction.py` |
| Cross-tenant leakage | RLS on chunks and embeddings | `test_tenant_isolation.py`, red-team rt21/rt22 |
| Unequal quality across languages | Per-language metrics in every eval report | eval-report.md "By language" |
| Model outage | Degraded mode hands over with a template summary | `test_degraded_mode.py`, `docs/drills/2026-10-23-model-down.md` |
| A trained model acting on a weak signal | Classifier in shadow mode only; promotion needs a client holdout | section 6, `test_classifier.py` |

## 8. Ethical considerations

The assistant states it is automated in its first message. It is designed to reduce unanswered
messages, not to replace staff: every handoff names a person. Customers who write in Sepedi
must not get a worse service; per-language results are tracked and a native-speaker review of
the glossary is an open action.
