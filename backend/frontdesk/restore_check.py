"""After a restore: is this database safe to point the app at? Read-only.

    python -m frontdesk.restore_check --admin-dsn postgresql://postgres:...@localhost:5433/frontdesk_restored \\
        --app-dsn postgresql://frontdesk_app:...@localhost:5433/frontdesk_restored --tenant demo
    python -m frontdesk.restore_check --data-api --cluster-arn <restored> --admin-secret-arn ... --app-secret-arn ... \\
        --profile fd-dev --tenant demo --restore-time 2026-10-23T05:00:00Z          (scripts/restore-drill.ps1)

A restore that brings the rows back but not the protections is worse than no restore: the app
would serve every tenant's data with nothing below it to stop a bug. So the check proves, on the
restored copy, the same things CI proves on a fresh database:

1. every packaged migration is recorded with the same SHA-256 (nothing missing, nothing edited);
2. every app table has forced RLS, a policy and a NOT NULL tenant_id;
3. the append-only triggers exist on the audit log, consents and the event stream;
4. the app role cannot bypass RLS and owns nothing;
5. connected as the app role with no tenant set, every table reads as empty;
6. per tenant, the newest event and audit row, which against the restore point give the data
   actually lost (the measured RPO).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import uuid
from collections.abc import Callable
from typing import Any

from .db import Database
from .db.lint_migrations import CATALOG_SQL, rls_catalog_violations
from .db.migrate import MIGRATIONS
from .tenancy import DEMO_TENANT_ID

Query = Callable[[str, dict | None], list[dict[str, Any]]]
APPEND_ONLY = {"audit_log": "audit_log_append_only", "consents": "consents_append_only",
               "conversation_events": "conversation_events_append_only"}
TENANT_TABLES = ["customers", "conversations", "messages", "conversation_events", "documents", "chunks", "audit_log", "consents"]


def admin_checks(query: Query) -> list[dict]:
    out = []
    applied = {r["filename"]: r["sha256"] for r in query("SELECT filename, sha256 FROM meta.schema_migrations", None)}
    expected = {f.name: hashlib.sha256(f.read_text().encode()).hexdigest() for f in sorted(MIGRATIONS.glob("*.sql"))}
    missing = sorted(set(expected) - set(applied))
    edited = sorted(f for f in expected if f in applied and applied[f] != expected[f])
    out.append({"check": "every migration applied, unedited", "ok": not missing and not edited,
                "detail": f"{len(applied)} applied" + (f"; missing {missing}" if missing else "") + (f"; edited {edited}" if edited else "")})
    bad = rls_catalog_violations(query(CATALOG_SQL, None))
    out.append({"check": "forced RLS, a policy and tenant_id on every app table", "ok": not bad, "detail": "; ".join(bad) or "all tables"})
    trig = {(r["table"], r["trigger"]) for r in query(
        """SELECT c.relname AS table, t.tgname AS trigger FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
           JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND NOT t.tgisinternal AND t.tgenabled <> 'D'""", None)}
    absent = [f"{t}.{g}" for t, g in APPEND_ONLY.items() if (t, g) not in trig]
    out.append({"check": "append-only triggers present and enabled", "ok": not absent, "detail": ", ".join(absent) or ", ".join(APPEND_ONLY)})
    role = query("""SELECT r.rolbypassrls AS bypass, r.rolsuper AS super,
                           (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'app' AND c.relowner = r.oid) AS owned
                    FROM pg_roles r WHERE r.rolname = 'frontdesk_app'""", None)
    ok = bool(role) and not role[0]["bypass"] and not role[0]["super"] and int(role[0]["owned"]) == 0
    out.append({"check": "app role cannot bypass RLS and owns nothing", "ok": ok, "detail": str(role[0]) if role else "frontdesk_app missing"})
    return out


def app_checks(db: Database, tenant_ids: list[uuid.UUID], restore_time: dt.datetime | None) -> list[dict]:
    out = []
    with db.transaction(None) as tx:
        counts = {t: int(tx.scalar(f"SELECT count(*) FROM app.{t}")) for t in TENANT_TABLES}  # nosemgrep -- fixed table names
    leaked = {t: n for t, n in counts.items() if n}
    out.append({"check": "with no tenant set, every table reads as empty", "ok": not leaked, "detail": json.dumps(leaked) if leaked else "0 rows in all"})
    for tid in tenant_ids:
        with db.transaction(tid) as tx:
            r = tx.one("""SELECT (SELECT count(*) FROM app.conversations) AS conversations, (SELECT count(*) FROM app.chunks) AS chunks,
                                 (SELECT max(occurred_at) FROM app.conversation_events) AS newest_event,
                                 (SELECT max(at) FROM app.audit_log) AS newest_audit""")
        newest = r["newest_event"]
        lost = ""
        if restore_time and newest is not None:
            newest_dt = newest if isinstance(newest, dt.datetime) else dt.datetime.fromisoformat(str(newest).replace(" ", "T"))
            if newest_dt.tzinfo is None:
                newest_dt = newest_dt.replace(tzinfo=dt.UTC)
            lost = f"; newest event {(restore_time - newest_dt).total_seconds() / 60:.1f} minutes before the restore point"
        out.append({"check": f"tenant {str(tid)[:8]} data present", "ok": int(r["chunks"]) > 0,
                    "detail": f"{r['conversations']} conversations, {r['chunks']} chunks{lost}"})
    return out


def report(checks: list[dict]) -> str:
    lines = ["| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {c['check']} | {'pass' if c['ok'] else '**FAIL**'} | {c['detail']} |" for c in checks]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from .db.migrate import data_api_runner, local_runner

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin-dsn"); ap.add_argument("--app-dsn")
    ap.add_argument("--data-api", action="store_true"); ap.add_argument("--cluster-arn")
    ap.add_argument("--admin-secret-arn"); ap.add_argument("--app-secret-arn"); ap.add_argument("--profile")
    ap.add_argument("--database", default="frontdesk")
    ap.add_argument("--tenant", action="append", default=[], help="'demo' or a tenant uuid; repeatable")
    ap.add_argument("--restore-time", help="ISO 8601 UTC restore point, to measure data lost")
    a = ap.parse_args(argv)
    tenants = [DEMO_TENANT_ID if t == "demo" else uuid.UUID(t) for t in a.tenant]
    when = dt.datetime.fromisoformat(a.restore_time.replace("Z", "+00:00")) if a.restore_time else None
    if a.data_api:
        import boto3

        from .db.dataapi import DataApiDatabase

        client = boto3.Session(profile_name=a.profile, region_name="af-south-1").client("rds-data")
        _, query = data_api_runner(a.cluster_arn, a.admin_secret_arn, a.database, client=client)
        db: Database = DataApiDatabase(a.cluster_arn, a.app_secret_arn, a.database, client=client)
    else:
        from .db.local import LocalDatabase

        _, query = local_runner(a.admin_dsn)
        db = LocalDatabase(a.app_dsn)
    checks = admin_checks(query) + app_checks(db, tenants, when)
    print(report(checks))
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
