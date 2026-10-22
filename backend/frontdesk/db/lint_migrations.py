"""Static checks on migrations, run in CI before any database exists.

A new table without row-level security is a failing migration. This linter catches it from
the SQL text; `rls_catalog_violations()` catches it again from the live catalog after the
migration runs, in case a table was created some way the regexes do not see.

Run:  python -m frontdesk.db.lint_migrations db/migrations
"""

from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import dataclass

from .sqlsplit import split_sql

APP_ROLE = "frontdesk_app"
APPEND_ONLY = {"audit_log", "conversation_events", "consents"}

CREATE_TABLE = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?app\.(\w+)\s*\((.*)\)\s*$", re.I | re.S)
ENABLE = re.compile(r"ALTER\s+TABLE\s+app\.(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I)
FORCE = re.compile(r"ALTER\s+TABLE\s+app\.(\w+)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", re.I)
POLICY = re.compile(r"CREATE\s+POLICY\s+\w+\s+ON\s+app\.(\w+)", re.I)
DISABLE = re.compile(r"(DISABLE\s+ROW\s+LEVEL\s+SECURITY|NO\s+FORCE\s+ROW\s+LEVEL\s+SECURITY)", re.I)
BYPASS = re.compile(r"\bBYPASSRLS\b", re.I)
GRANT = re.compile(r"GRANT\s+(.+?)\s+ON\s+(?:TABLE\s+)?app\.(\w+)\s+TO\s+(\w+)", re.I | re.S)
TENANT_COL = re.compile(r"\btenant_id\s+uuid\s+NOT\s+NULL", re.I)


@dataclass(frozen=True)
class Finding:
    file: str
    table: str
    problem: str

    def __str__(self) -> str:
        return f"{self.file}: app.{self.table}: {self.problem}"


def lint_text(name: str, sql: str) -> list[Finding]:
    findings: list[Finding] = []
    stmts = split_sql(sql)
    tables: dict[str, str] = {}
    enabled, forced, policied = set(), set(), set()
    for s in stmts:
        if m := CREATE_TABLE.match(s):
            tables[m.group(1)] = m.group(2)
        enabled.update(ENABLE.findall(s))
        forced.update(FORCE.findall(s))
        policied.update(POLICY.findall(s))
        if DISABLE.search(s):
            findings.append(Finding(name, "*", f"turns row-level security off: {s[:80]}"))
        if BYPASS.search(s) and re.search(r"\bNOBYPASSRLS\b", s, re.I) is None:
            findings.append(Finding(name, "*", "grants BYPASSRLS"))
        for g in GRANT.finditer(s):
            privs, table, role = g.group(1).upper(), g.group(2), g.group(3)
            if role != APP_ROLE:
                continue
            if "TRUNCATE" in privs or "ALL" in privs.split(","):
                findings.append(Finding(name, table, f"grants {privs} to {role}"))
            if table in APPEND_ONLY and ("UPDATE" in privs or "DELETE" in privs):
                findings.append(Finding(name, table, "append-only table granted UPDATE/DELETE"))
    for t, body in tables.items():
        if not TENANT_COL.search(body):
            findings.append(Finding(name, t, "has no 'tenant_id uuid NOT NULL' column"))
        if t not in enabled:
            findings.append(Finding(name, t, "row-level security not enabled"))
        if t not in forced:
            findings.append(Finding(name, t, "row-level security not forced (owner would bypass it)"))
        if t not in policied:
            findings.append(Finding(name, t, "no RLS policy"))
    return findings


def lint_dir(path: pathlib.Path) -> list[Finding]:
    out: list[Finding] = []
    for f in sorted(path.glob("*.sql")):
        out.extend(lint_text(f.name, f.read_text()))
    return out


CATALOG_SQL = """
SELECT c.relname AS table,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS rls_forced,
       EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy,
       EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
               AND NOT a.attisdropped AND a.attnotnull) AS has_tenant_id
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
ORDER BY 1
"""


def rls_catalog_violations(rows: list[dict]) -> list[str]:
    bad = []
    for r in rows:
        missing = [k for k in ("rls_enabled", "rls_forced", "has_policy", "has_tenant_id") if not r[k]]
        if missing:
            bad.append(f"app.{r['table']}: missing {', '.join(missing)}")
    return bad


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = pathlib.Path(argv[0] if argv else "db/migrations")
    findings = lint_dir(path)
    for f in findings:
        print(f"MIGRATION LINT: {f}")
    print(f"{len(findings)} finding(s) in {path}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
