# AGENTS.md: working agreements for Front Desk

Read this before changing anything. It replaces re-explaining the project in every session.

## Who this is for

The owner is Machethe DM (Limpopo, South Africa), finishing an MSc with a security
specialisation. This product has to work for real South African SMEs **and** hold up in an
interview for AI Engineer, Data Scientist, Cloud and Cyber Security, DevSecOps, Data and AI
Security, and Forward Deployed Engineer roles. When two approaches are close, pick the one that
leaves evidence: a measured number, a documented threat, a test that fails the build.

## Non-negotiables

1. **Nothing exists only in a README.** If a technology is named, it is wired up and tested.
   If it cannot be wired up yet (needs a credential, a real client, a paid service), leave a
   clearly marked seam: `# SEAM(<name>): ...` in code and a row in `docs/DEPTH.md` saying so.
2. **Commands for the owner are PowerShell** (Windows). Use `;` not `&&`,
   `Remove-Item -Recurse -Force` not `rm -rf`, `$env:VAR = "x"` not `export`.
   Every deploy step says exactly what to paste and what the owner should see.
3. **After each phase**, add a plain-language note in `docs/learning/`: what was run, why it
   works, and per section one line to say in an interview plus one self-check question.
4. **Keep `docs/DEPTH.md` current.** An item is done only when its evidence link exists.

## The refusals (decided; if a request implies one, say so and stop)

- It never pretends to be a person. The first message says it is an assistant; "talk to a
  person" always works.
- It answers only from the tenant's approved sources. No source, no answer: say so, hand over.
  It never invents a price, date or policy.
- It makes no commitments (refunds, discounts, credit, bookings) beyond what a tool allows.
- No marketing without opt-in. Consent to be answered and consent to be marketed to are
  separate records (POPIA s69).
- Never guess a customer's title or pronoun. Use the name as given.
- No training across tenants without written consent. Demo data lives in its own tenant,
  behind a flag, and a test asserts it never reaches a real tenant.

## Rules that hold in code (and the test that proves each)

| Rule | Where it is enforced | Proof |
|---|---|---|
| Tenant isolation below the app | Postgres RLS on every business table, `FORCE ROW LEVEL SECURITY`, app role is not owner | `backend/tests/test_tenant_isolation.py`, `backend/frontdesk/db/lint_migrations.py` |
| A new table without RLS fails | Migration linter + catalog check | `backend/tests/test_migration_lint.py` |
| Reads are audited, audit is append-only | `audit_log` with trigger rejecting UPDATE/DELETE, read paths call `audit.record_read` | `backend/tests/test_audit.py` |
| Conversations are events | `conversation_events`; dashboards computed from events | `backend/tests/test_insights.py` |
| Model down, front desk open | Agent `DEGRADED` path; Step Functions `Catch` | `backend/tests/test_degraded_mode.py` |
| Customer text is hostile | PII redaction before any model call, Guardrails, schema-validated output, injection suite | `backend/tests/test_redaction.py`, `evals/redteam/` |
| Models are measured | Floors in `docs/PLAN.md` parsed by `ml/frontdesk_ml/gates.py` | CI job `evals` |
| No long-lived AWS keys | GitHub OIDC roles; SCP denies IAM users and access keys | `infra/test/scp.test.ts` |
| Writes to real tenants preview first | Scripts take `--dry-run` (default) before `--write`, re-check preconditions at write time | `backend/tests/test_write_guard.py` |

## How we write code

- Tests are named as sentences: `test_a_conversation_from_tenant_a_is_invisible_to_tenant_b`.
- Comments explain the decision and what goes wrong without it, not what the line does.
- Python 3.13 for the backend and ML (`backend/`, `ml/`), TypeScript for CDK (`infra/`) and
  the staff inbox (`apps/inbox`). One SQL migration per change in `db/migrations/`.
- Secrets live in SSM Parameter Store SecureString. The only Secrets Manager secrets are the
  two Aurora credentials (admin for migrations, app for Lambdas), because the RDS Data API
  requires Secrets Manager (ADR 0004).
- Never commit real customer data. Fixtures are synthetic and say so.

## Regions (ADR 0002)

- `af-south-1` (Cape Town): data at rest, compute, WhatsApp, Cognito, logs.
- `eu-central-1` (Frankfurt): Bedrock only (Claude, embeddings, rerank, Guardrails), because
  af-south-1 offers Claude only through *Global* routing and has no embeddings, rerank or
  Guardrails. Only redacted text crosses; the POPIA s72 note is `docs/POPIA-S72.md`.
- `us-east-1`: global control planes only (IAM, Organizations, Budgets, CloudFront, ACM).
  An SCP denies everything else.

## Keep going

When asked to implement or continue a phase: finish the slice, then start the next slice of
that phase in the same session. Stop only when the phase is finished, or the next step needs a
credential, a real external service, or a choice the plan does not settle.

## Local commands (bash in CI, PowerShell for the owner, see docs/DEPLOY.md)

```powershell
# one-time
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e "backend[dev]" -e ml
# database for tests (Docker Desktop)
docker compose up -d db
# everything CI runs
make check   # or, without make: .\scripts\check.ps1
```
