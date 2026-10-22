# ADR 0001: One repository; Python for backend and ML, TypeScript for CDK and the inbox

- Status: accepted
- Date: 2026-10-22

## Context
The live answer path and the offline replay must run the *same* retrieval, redaction and agent
code, or the replay score says nothing about production. The data-science work (classifier,
clustering, forecasting, judge calibration) is Python. CDK is required in TypeScript. The staff
inbox is a web app.

## Decision
- `backend/` (Python 3.13): the `frontdesk` package. Lambdas import it; the replay CLI imports it.
- `ml/` (Python 3.13): the `frontdesk_ml` package: metrics, gates, judge, classifier, insights.
- `infra/` (TypeScript): CDK app, SCPs, cdk-nag, jest tests.
- `apps/inbox` (TypeScript, Next.js static export), `apps/widget` (plain JS, no build step).
- `db/migrations/`: plain SQL, run by `backend/frontdesk/db/migrate.py`.

## Consequences
Two toolchains in CI. In exchange, the replay is a faithful rehearsal of production, and no
ML code is ported between languages. Revisit if Lambda cold starts for the Python package
exceed 1.5 s at p95 (measured in Phase 2).
