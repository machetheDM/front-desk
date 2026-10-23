"""Apply SQL migrations in order, then refuse to finish if any app table lacks RLS.

Runs as the migrating user (local `postgres`, or the Aurora master secret through the Data
API). Each file is applied in its own transaction and recorded in meta.schema_migrations with
its SHA-256, so an edited, already-applied migration is detected instead of silently skipped.

    python -m frontdesk.db.migrate --dsn postgresql://postgres@localhost:5433/frontdesk
    python -m frontdesk.db.migrate --data-api --cluster-arn ... --secret-arn ...   (in AWS)
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
from collections.abc import Callable
from typing import Any

from .lint_migrations import CATALOG_SQL, lint_dir, rls_catalog_violations
from .sqlsplit import split_sql

ROOT = pathlib.Path(__file__).resolve().parents[3]
# In the Lambda package the SQL files are copied next to the code (scripts/build-lambda.sh).
_PACKAGED = pathlib.Path(__file__).resolve().parents[1] / "migrations"
MIGRATIONS = _PACKAGED if _PACKAGED.exists() else ROOT / "db" / "migrations"

Exec = Callable[[str, dict[str, Any] | None], list[dict[str, Any]]]


class MigrationError(RuntimeError):
    pass


BOOTSTRAP = [
    "CREATE SCHEMA IF NOT EXISTS meta",
    """CREATE TABLE IF NOT EXISTS meta.schema_migrations (
         filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""",
]


def apply(run_tx: Callable[[list[tuple[str, dict | None]]], list[list[dict]]], query: Exec,
          directory: pathlib.Path = MIGRATIONS) -> list[str]:
    """`run_tx` executes a list of statements in one transaction; `query` runs one read."""
    findings = lint_dir(directory)
    if findings:
        raise MigrationError("migration lint failed:\n" + "\n".join(map(str, findings)))
    run_tx([(s, None) for s in BOOTSTRAP])
    applied = {r["filename"]: r["sha256"] for r in query("SELECT filename, sha256 FROM meta.schema_migrations", None)}
    done = []
    for f in sorted(directory.glob("*.sql")):
        text = f.read_text()
        digest = hashlib.sha256(text.encode()).hexdigest()
        if f.name in applied:
            if applied[f.name] != digest:
                raise MigrationError(f"{f.name} was edited after it was applied; add a new migration instead")
            continue
        stmts = [(s, None) for s in split_sql(text)]
        stmts.append(("INSERT INTO meta.schema_migrations (filename, sha256) VALUES (:f, :h)", {"f": f.name, "h": digest}))
        run_tx(stmts)
        done.append(f.name)
    bad = rls_catalog_violations(query(CATALOG_SQL, None))
    if bad:
        raise MigrationError("tables without forced RLS and a policy:\n" + "\n".join(bad))
    return done


def local_runner(dsn: str):
    import psycopg
    from psycopg.rows import dict_row

    from .local import to_pyformat

    def run_tx(stmts):
        out = []
        with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            for sql, params in stmts:
                # Without params psycopg does no placeholder parsing, so raw SQL passes untouched.
                cur.execute(to_pyformat(sql) if params else sql, params)
                out.append(cur.fetchall() if cur.description else [])
        return out

    def query(sql, params):
        with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    return run_tx, query


def data_api_runner(cluster_arn: str, secret_arn: str, database: str, client: Any = None):
    import json

    from .dataapi import call_resuming, to_sql_parameters

    if client is None:
        import boto3

        client = boto3.client("rds-data")
    common = dict(resourceArn=cluster_arn, secretArn=secret_arn)

    def run_tx(stmts):
        tx = call_resuming(client.begin_transaction, database=database, **common)["transactionId"]
        out = []
        try:
            for sql, params in stmts:
                r = client.execute_statement(database=database, sql=sql, parameters=to_sql_parameters(params),
                                             transactionId=tx, formatRecordsAs="JSON", **common)
                out.append(json.loads(r.get("formattedRecords") or "[]"))
        except Exception:
            client.rollback_transaction(transactionId=tx, **common)
            raise
        client.commit_transaction(transactionId=tx, **common)
        return out

    def query(sql, params):
        r = call_resuming(client.execute_statement, database=database, sql=sql, parameters=to_sql_parameters(params),
                          formatRecordsAs="JSON", **common)
        return json.loads(r.get("formattedRecords") or "[]")

    return run_tx, query


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn")
    ap.add_argument("--data-api", action="store_true")
    ap.add_argument("--cluster-arn")
    ap.add_argument("--secret-arn")
    ap.add_argument("--database", default="frontdesk")
    ap.add_argument("--dir", default=str(MIGRATIONS))
    a = ap.parse_args(argv)
    if a.data_api:
        run_tx, query = data_api_runner(a.cluster_arn, a.secret_arn, a.database)
    elif a.dsn:
        run_tx, query = local_runner(a.dsn)
    else:
        ap.error("--dsn or --data-api is required")
    try:
        done = apply(run_tx, query, pathlib.Path(a.dir))
    except MigrationError as e:
        print(f"MIGRATION FAILED: {e}", file=sys.stderr)
        return 1
    print(f"applied {len(done)} migration(s): {', '.join(done) or 'none'}; RLS catalog check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
