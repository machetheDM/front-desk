# Evaluation report: offline pipeline

Generated 2026-10-22T23:46:34.690288+00:00 from `evals/datasets/bushveld.synthetic.yaml`.
Headline (gated) = **test split**. **Holdout** = questions written after tuning and never tuned on:
the honest estimate (docs/MODEL-CARD.md). Dev = the split thresholds were tuned on.

| Metric | Test (gated) | Holdout | Dev |
|---|---|---|---|
| Retrieval recall@5 | 95.9% | 88.0% | 100.0% |
| Retrieval recall@1 | 89.8% | 64.0% | 92.0% |
| Retrieval MRR | 92.3% | 75.8% | 96.0% |
| Answer correctness | 78.4% | 52.0% | 92.3% |
| Groundedness of sent answers | 100.0% | 100.0% | 100.0% |
| Correct answer-or-handoff decision | 85.1% | 70.0% | 94.4% |
| Unanswerable questions handed over | 87.5% | 83.3% | 100.0% |
| Forbidden facts in replies | 0.0% | 0.0% | 0.0% |
| PII patterns reaching a model | 0 | 0 | 0 |
| Share answered | 56.8% | 37.5% | 66.7% |

Red-team attack success rate: **0.0%** (0 of 40 cases).

Judge calibration: needs at least 30 human labels (have 0): run python -m frontdesk_ml.label.

## By language (dev and test splits)

| Language | n | recall@5 | correctness | decision |
|---|---|---|---|---|
| af-en | 3 | 100.0% | 100.0% | 100.0% |
| en | 95 | 96.7% | 82.8% | 88.4% |
| nso-en | 12 | 100.0% | 80.0% | 83.3% |

## Cost and latency by route (test split)

| Route | Answers | USD per answer | p50 latency (ms) |
|---|---|---|---|
| small | 56 | 0.000000 | 6.1 |
| large | 1 | 0.000000 | 7.5 |

## Red team by OWASP LLM Top 10 category

| Category | Cases | Succeeded |
|---|---|---|
| LLM01 | 14 | 0 |
| LLM02 | 4 | 0 |
| LLM04 | 1 | 0 |
| LLM06 | 7 | 0 |
| LLM07 | 4 | 0 |
| LLM08 | 2 | 0 |
| LLM09 | 7 | 0 |
| LLM10 | 1 | 0 |

## Red team by MITRE ATLAS technique

| Technique | Cases | Succeeded |
|---|---|---|
| AML.T0034 | 1 | 0 |
| AML.T0048 | 10 | 0 |
| AML.T0051.000 | 9 | 0 |
| AML.T0051.001 | 2 | 0 |
| AML.T0053 | 3 | 0 |
| AML.T0054 | 4 | 0 |
| AML.T0056 | 4 | 0 |
| AML.T0057 | 6 | 0 |
| AML.T0070 | 1 | 0 |
