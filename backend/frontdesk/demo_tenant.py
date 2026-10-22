"""Load the demo tenant (Bushveld Solar & Electrical, made up) into a database.

Refusal 6: demo data never reaches a real tenant. This loader can only target DEMO_TENANT_ID,
the content is flagged as demo, and the ingestion precondition re-checks the tenant's demo flag
inside the writing transaction.

    python -m frontdesk.demo_tenant --target local --dsn postgresql://frontdesk_app:...@localhost:5433/frontdesk --write
    python -m frontdesk.demo_tenant --target aws --profile fd-dev --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

from .db import Database
from .retrieval.embedders import Embedder, by_name
from .retrieval.ingest import plan_ingest, read_docs
from .tenancy import DEMO_TENANT_ID, DemoDataLeak, create_tenant
from .write_guard import WritePlan, add_write_flags, execute

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "demo" / "bushveld"


def load_spec() -> dict:
    return yaml.safe_load((DEMO_DIR / "tenant.yaml").read_text())


def ensure_tenant(db: Database, spec: dict, *, write: bool) -> None:
    with db.transaction(DEMO_TENANT_ID) as tx:
        exists = tx.one("SELECT is_demo FROM app.tenants")
    if exists:
        if not exists["is_demo"]:
            raise DemoDataLeak("DEMO_TENANT_ID exists but is not flagged as demo; refusing")
        return
    print(f"PLAN: create demo tenant {DEMO_TENANT_ID} '{spec['tenant']['name']}'")
    if write:
        t = spec["tenant"]
        create_tenant(db, t["name"], t["slug"], is_demo=True, tenant_id=DEMO_TENANT_ID, assistant_name=t["assistant_name"])


def plan_staff_and_config(spec: dict) -> WritePlan:
    def precondition(tx):
        row = tx.one("SELECT is_demo FROM app.tenants")
        return None if row and row["is_demo"] else "target is not the demo tenant"

    def apply(tx):
        for s in spec["staff"]:
            tx.execute(
                """INSERT INTO app.staff (tenant_id, display_name, email, role, handles_topics)
                   VALUES (app.current_tenant(), :n, :e, :r, ARRAY(SELECT jsonb_array_elements_text(:t::jsonb)))
                   ON CONFLICT (tenant_id, email) DO UPDATE SET display_name = EXCLUDED.display_name,
                     role = EXCLUDED.role, handles_topics = EXCLUDED.handles_topics""",
                {"n": s["display_name"], "e": s["email"], "r": s["role"], "t": s["handles_topics"]},
            )
        for key, value in [("service_zones", spec["config"]["service_zones"]), ("office_hours", spec["tenant"]["office_hours"]),
                           ("orders_demo", spec["orders"])]:
            tx.execute(
                """INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), :k, :v::jsonb, 'demo loader')
                   ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                {"k": key, "v": json.dumps(value)},
            )
        return {"staff": len(spec["staff"]), "config_keys": 3}

    items = [f"staff: {s['display_name']} ({s['role']})" for s in spec["staff"]] + ["config: service_zones, office_hours, orders_demo"]
    return WritePlan("demo staff and configuration", DEMO_TENANT_ID, items, precondition, apply)


def load(db: Database, embedder: Embedder, *, write: bool) -> None:
    spec = load_spec()
    ensure_tenant(db, spec, write=write)
    if not write:
        print("DRY RUN: the demo tenant would be created first; the plans below assume it exists.")
    execute(db, plan_staff_and_config(spec), write=write)
    docs = read_docs(sorted((DEMO_DIR / "docs").glob("*.md")), uri_prefix="demo://bushveld/")
    plan = plan_ingest(DEMO_TENANT_ID, docs, embedder, approved_by="Owner (demo)", is_demo_content=True)
    try:
        execute(db, plan, write=write)
    except Exception as e:  # already loaded is fine when re-running
        if "already loaded" in str(e):
            print(f"SKIPPED: {e}")
        else:
            raise


def connect_target(args) -> Database:
    if args.target == "local":
        from .db.local import LocalDatabase

        return LocalDatabase(args.dsn)
    import boto3

    from .db.dataapi import DataApiDatabase

    session = boto3.Session(profile_name=args.profile, region_name="af-south-1")
    outs = {o["OutputKey"]: o["OutputValue"] for o in
            session.client("cloudformation").describe_stacks(StackName=f"FrontDesk-{args.env}-Data")["Stacks"][0]["Outputs"]}
    return DataApiDatabase(outs["ClusterArn"], outs["AppSecretArn"], "frontdesk", client=session.client("rds-data"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["local", "aws"], default="local")
    ap.add_argument("--dsn")
    ap.add_argument("--profile")
    ap.add_argument("--env", default="dev")
    ap.add_argument("--embedder", default="hashing-v1", help="hashing-v1 offline; cohere-embed-multilingual-v3 on Bedrock")
    add_write_flags(ap)
    a = ap.parse_args(argv)
    if a.target == "aws" and a.env != "dev":
        print("The demo tenant belongs in dev only.", file=sys.stderr)
        return 2
    load(connect_target(a), by_name(a.embedder), write=a.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
