# Front Desk

An AI front desk for South African small businesses, on WhatsApp and the web, built only on
AWS. It answers customers only from the business's approved documents, citing them; hands
everything else to a named staff member with a summary; and shows the owner what customers
ask, what was answered after hours, and what the documents are missing.

The first thing it sells is the **replay**: last month's real questions, answered offline from
the client's own documents before any contract, with the gaps listed
(`docs/evidence/replay-demo-offline/owner-report.md`, on a made-up company).

## What it refuses to do

Built in and tested, not settings (`AGENTS.md`):

- pretend to be a person (the first message says it is an assistant; PERSON always reaches one);
- answer without a source, or invent a price, date or policy;
- make a commitment (discount, refund, credit, booking) beyond what a tool allows;
- market to anyone who has not opted in separately (POPIA s69);
- guess a title or pronoun;
- train on one client's data for another.

## Status

Built and tested in code, Phases 0 to 6 (`docs/PLAN.md`). What is left needs things code
cannot supply: an AWS account set up by the owner (`docs/DEPLOY.md`, about 20 PowerShell steps),
Meta business verification for WhatsApp, and a real client for the 30-day pilot. Every such
step is marked in code as `SEAM(name)` and listed in `docs/DEPTH.md`, the acceptance checklist
with an evidence link per item.

## Numbers, with their evidence

| What | Result | Evidence |
|---|---|---|
| Retrieval recall@5, holdout written after tuning | 0.88 (test split 0.98) | `docs/evidence/eval-offline-2026-10-23.md` |
| Groundedness of sent answers | 1.00 on every split | same |
| Red-team attack success, 42 cases mapped to OWASP LLM 2025 and MITRE ATLAS | 0 of 42 (offline pipeline) | `docs/AI-SECURITY.md` |
| Trained handoff classifier vs hand-written rules | rules win on both splits; model runs in shadow mode | `docs/evidence/classifier-comparison.md` |
| Weekly drift alarm: false alarms when nothing changed | about 2% of weeks (the textbook rule: 92%) | `docs/evidence/insights-demo.md` |
| Volume forecast against "same as last week" | MASE 0.86 | same |
| Hosting while idle / during a demo | about R84 / R270 a month | `docs/ARCHITECTURE.md` |

The production pipeline (Claude on Bedrock) is **not yet measured**; the offline extractive
pipeline is the CI floor, not the product (`docs/MODEL-CARD.md`, ADR 0006 and 0008).

## How it is built

- **Regions:** data and compute in Cape Town (af-south-1); Bedrock in Frankfurt (eu-central-1)
  because Cape Town has Claude only through global routing and no embeddings, rerank or
  Guardrails; only redacted text crosses (`docs/POPIA-S72.md`, ADR 0002).
- **Tenant isolation below the app:** Aurora PostgreSQL Serverless v2 with forced row-level
  security on every table, pgvector and full-text search in the same protected rows, a non-owner
  app role, Data API only (`backend/tests/test_tenant_isolation.py`, ADR 0004).
- **Agent:** redaction first, rules, typed tenant-scoped tools, hybrid retrieval with reranking,
  model routing, a schema-validated answer, and a validator that rejects uncited facts, links,
  commitments and titles (`backend/frontdesk/agent/state_machine.py`, `backend/frontdesk/llm/validate.py`).
- **Model down, front desk open:** Step Functions catches any failure and a person is named
  (`docs/drills/2026-10-23-model-down.md`, run on every pull request).
- **Delivery:** AWS Organizations with tested SCPs, GitHub OIDC (no stored keys), cdk-nag,
  cfn-guard, Semgrep, gitleaks, Trivy, SBOM, signed Lambda code with canary rollback, and every
  gate kept with a failing example (`.github/workflows/ci.yml`, `security/gate-selftest.sh`).

## Try it locally (PowerShell, Docker Desktop, Python 3.13)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements/dev/requirements.txt -e "backend[dev]" -e "ml[dev]"
docker compose up -d db
$env:FRONTDESK_TEST_ADMIN_DSN = "postgresql://postgres:local-only@localhost:5433/postgres"
pytest -q backend/tests ml/tests
python -m frontdesk_ml.run_evals --pipeline offline --out reports\local
python -m frontdesk_ml.gates --pipeline offline --metrics reports\local\metrics.json
```

You should see every test pass, then `quality gate passed`; the report is
`reports\local\eval-report.md`. Everything CI runs: `.\scripts\check.ps1`.

## Where things are

| Folder | What |
|---|---|
| `backend/frontdesk/` | Lambdas, agent, retrieval, channels, inbox API, drills, admin commands |
| `ml/frontdesk_ml/` | Evaluation, red-team, classifier, clustering, forecast, A/B, ROI |
| `infra/` | CDK app: organization, SCPs, workload stacks, detections, tests |
| `apps/inbox/`, `apps/widget/` | Staff inbox (static Next.js, Cognito PKCE) and the website widget |
| `db/migrations/` | One SQL migration per change; a table without forced RLS fails the build |
| `evals/`, `experiments/` | Labelled questions, red-team cases, synthetic training data, A/B pre-registrations |
| `docs/` | Plan, architecture, threat model, AI security, detections, incident plan, drills, ADRs, engagement pack, learning notes |

Everything in `demo/` and `evals/` is synthetic and says so. Bushveld Solar & Electrical does
not exist.
