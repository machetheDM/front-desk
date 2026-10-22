#!/usr/bin/env bash
# Each security gate must FAIL on a known-bad example. If a scanner is misconfigured (wrong
# path, rule removed, exit code ignored), it silently passes everything; this script is what
# notices. Run in CI (job gate-selftest) and locally: bash security/gate-selftest.sh
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
expect_fail() {
  local name="$1"; shift
  if "$@" >/tmp/gate-$name.log 2>&1; then
    echo "GATE BROKEN: $name passed a known-bad example"; tail -20 /tmp/gate-$name.log; fail=1
  else
    echo "ok: $name rejects its failing example"
  fi
}
expect_pass() {
  local name="$1"; shift
  if "$@" >/tmp/gate-$name.log 2>&1; then echo "ok: $name passes the clean example"
  else echo "GATE TOO STRICT: $name failed a clean example"; tail -20 /tmp/gate-$name.log; fail=1; fi
}

# gitleaks: scan the fixture without the allowlist that hides it from the normal scan.
cfg=$(mktemp); sed '/^\[allowlist\]/,$d' .gitleaks.toml > "$cfg"
expect_fail gitleaks gitleaks dir security/gate-fixtures/gitleaks --config "$cfg" --no-banner --exit-code 1

# semgrep: every custom rule must fire on bad.py (six findings expected).
semgrep_rules_fired() {
  # Returns non-zero (the gate "rejects") only if all six custom rules fire.
  local ids
  ids=$(semgrep --config security/semgrep --json --quiet security/gate-fixtures/semgrep/bad.py \
    | python3 -c 'import json,sys; print(len({x["check_id"].split(".")[-1] for x in json.load(sys.stdin)["results"]}))')
  echo "semgrep custom rules fired: $ids of 6"
  [ "$ids" -ge 6 ] && return 1 || return 0
}
expect_fail semgrep semgrep_rules_fired

# trivy: a manifest with a known critical CVE, generated here so no vulnerable manifest is
# ever committed (Dependabot would flag it).
tdir=$(mktemp -d); printf 'pyyaml==5.3\n' > "$tdir/requirements.txt"
if command -v trivy >/dev/null; then
  expect_fail trivy trivy fs --quiet --exit-code 1 --severity CRITICAL --scanners vuln "$tdir"
else
  echo "skip: trivy not installed locally (runs in CI)"
fi

# cfn-guard: the rules' own unit tests include a failing input for every rule.
expect_pass cfn-guard-unit-tests cfn-guard test --rules-file infra/policies/guard/frontdesk.guard --test-data infra/policies/guard/tests/frontdesk_tests.yaml
bad=$(mktemp --suffix .json); echo '{"Resources":{"U":{"Type":"AWS::IAM::User","Properties":{}}}}' > "$bad"
expect_fail cfn-guard cfn-guard validate --rules infra/policies/guard/frontdesk.guard --data "$bad"

# migration linter: the kept bad migration must fail.
expect_fail migration-lint python -m frontdesk.db.lint_migrations backend/tests/fixtures/bad_migrations

# quality gate: metrics below the floors in docs/PLAN.md must fail.
expect_fail eval-gate python -m frontdesk_ml.gates --pipeline offline --metrics ml/tests/fixtures/metrics_below_floor.json

exit $fail
