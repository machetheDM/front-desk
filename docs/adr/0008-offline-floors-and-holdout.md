# ADR 0008: Offline quality floors are regression floors; a clean holdout carries the honest number

- Status: accepted
- Date: 2026-10-22

## Context
The floors in docs/PLAN.md were written before anything was measured. The first full run of
the offline (deterministic, extractive) pipeline gave, on the test split of
`evals/datasets/bushveld.synthetic.yaml` after tuning on its dev split:

| Metric | Pre-registered floor | Measured (test) | Measured (clean holdout, 40 new questions) |
|---|---|---|---|
| recall@5 | 0.85 | 0.96 | 0.88 |
| MRR | 0.65 | 0.92 | 0.76 |
| answer correctness | 0.70 | 0.78 | 0.52 |
| groundedness | 0.95 | 1.00 | 1.00 |
| correct answer-or-handoff decision | 0.90 | 0.85 | 0.70 |
| unanswerable handed over | 1.00 | 0.875 | 0.83 |
| PII reaching a model | 0 | 0 | 0 |
| red-team attack success | 0 | 0 | 0 (suite run once, not split) |

Two lessons. First, a word-overlap heuristic cannot decide answerability as well as the
pre-registered floors assumed; the decision floors were wishes. Second, the test split had been
inspected during development (the first run printed test failures), so it is no longer a clean
estimate; the holdout, written after tuning and run once, shows the real generalisation gap.

Important: when the offline pipeline answers an unanswerable question it still only *quotes an
approved chunk* (groundedness 1.00); it does not invent. The failure is relevance, not
fabrication.

## Decision
- The **offline** floors become regression floors: measured test-split value minus a small
  margin. Their job is to fail a pull request that makes retrieval, redaction, policy or
  validation worse, not to claim product quality.
- The **Bedrock** floors stay as pre-registered. They are the product's quality bar and are
  checked on release against the production pipeline.
- Every evaluation reports dev, test (gated) and holdout separately. The holdout is never
  tuned on; when it has been looked at too often it is retired and a new one written.
- PII reaching a model and red-team attack success stay at zero for both pipelines.

## Consequences
The offline numbers must never be quoted as the product's accuracy. The honest offline
estimate is the holdout row above. The case for the Bedrock pipeline is made with its own
release evaluation (`.github/workflows/release-evals.yml`), which needs the dev account.
