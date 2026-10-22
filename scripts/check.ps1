<#
.SYNOPSIS
  Runs what CI runs, on Windows. Start the database first: docker compose up -d db
#>
$ErrorActionPreference = "Stop"
$env:FRONTDESK_TEST_ADMIN_DSN = "postgresql://postgres:local-only@localhost:5433/postgres"
python -m frontdesk.db.lint_migrations db/migrations
pytest -q backend/tests ml/tests
python -m frontdesk_ml.run_evals --pipeline offline --out reports/local
python -m frontdesk_ml.gates --pipeline offline --metrics reports/local/metrics.json
Push-Location infra; npx tsc --noEmit; npx jest; npx cdk synth --quiet -c frontdesk:ci=true -c frontdesk:testing=true; Pop-Location
Push-Location apps/inbox; npm run typecheck; npm test; npm run build; Pop-Location
Write-Host "All local checks passed. Security scanners run in CI (or: bash security/gate-selftest.sh in WSL)."
