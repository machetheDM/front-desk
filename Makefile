# Same steps as scripts/check.ps1, for CI and WSL/Linux.
DSN ?= postgresql://postgres@localhost:5433/postgres
.PHONY: check backend evals infra inbox selftest
check: backend evals infra inbox
backend:
	python -m frontdesk.db.lint_migrations db/migrations
	FRONTDESK_TEST_ADMIN_DSN=$(DSN) pytest -q backend/tests ml/tests
evals:
	FRONTDESK_TEST_ADMIN_DSN=$(DSN) python -m frontdesk_ml.run_evals --pipeline offline --out reports/local
	python -m frontdesk_ml.gates --pipeline offline --metrics reports/local/metrics.json
infra:
	cd infra && npx tsc --noEmit && npx jest && npx cdk synth --quiet -c frontdesk:ci=true -c frontdesk:testing=true
inbox:
	cd apps/inbox && npm run typecheck && npm test && npm run build
selftest:
	bash security/gate-selftest.sh
