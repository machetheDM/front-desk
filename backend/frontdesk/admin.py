"""Operator commands. Every command that writes prints its plan, and writes only with --write.

    python -m frontdesk.admin create-tenant --profile fd-dev --name "Acme Solar" --slug acme --dry-run
    python -m frontdesk.admin load-docs --profile fd-dev --tenant <uuid> --dir C:\\clients\\acme\\docs --approved-by "Owner name" --dry-run
    python -m frontdesk.admin create-staff --profile fd-dev --pool <pool id> --tenant demo --email ... --name ... --role owner --dry-run
    python -m frontdesk.admin map-whatsapp --profile fd-dev --tenant demo --phone-number-id phone-number-id-... --dry-run
    python -m frontdesk.admin create-widget-key --profile fd-dev --tenant demo --origin https://www.example.co.za --dry-run
    python -m frontdesk.admin start-experiment --profile fd-dev --tenant <uuid> --prereg experiments/<id>.yaml --approved-by "Owner name" --dry-run
    python -m frontdesk.admin stop-experiment --profile fd-dev --tenant <uuid> --id <id> --reason "guardrail breached" --dry-run
    python -m frontdesk.admin pause-assistant --profile fd-dev --tenant <uuid> --reason "INC-... wrong prices" --by "Machethe DM" --dry-run
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid

from . import routing
from .tenancy import DEMO_TENANT_ID, create_tenant
from .write_guard import WritePlan, add_write_flags, execute


def _tenant(v: str) -> uuid.UUID:
    return DEMO_TENANT_ID if v == "demo" else uuid.UUID(v)


def _session(profile: str | None):
    import boto3

    return boto3.Session(profile_name=profile, region_name="af-south-1")


def _db(a):
    from .demo_tenant import connect_target

    a.target = "local" if a.dsn else "aws"
    return connect_target(a)


def _ssm_put(session, env: str, name: str, value: dict, write: bool) -> None:
    full = f"/frontdesk/{env}/{name}"
    print(f"PLAN: SSM {full} = {json.dumps(value)}")
    if write:
        session.client("ssm").put_parameter(Name=full, Value=json.dumps(value), Type="String", Overwrite=True)
        print("WRITTEN")
    else:
        print("DRY RUN: nothing written. Re-run with --write.")


def cmd_create_tenant(a) -> int:
    where = "in the local database only" if a.dsn else f"then add it to /frontdesk/{a.env}/tenants"
    print(f"PLAN: create tenant '{a.name}' ({a.slug}), {where}")
    if not a.write:
        print("DRY RUN: nothing written. Re-run with --write.")
        return 0
    t = create_tenant(_db(a), a.name, a.slug)
    if a.dsn:  # a laptop replay: nothing in AWS knows about this tenant, and nothing needs to
        print(f"tenant id: {t.tenant_id} (local only)")
        return 0
    s = _session(a.profile)
    try:
        ids = json.loads(s.client("ssm").get_parameter(Name=f"/frontdesk/{a.env}/tenants")["Parameter"]["Value"])["tenant_ids"]
    except Exception:
        ids = []
    _ssm_put(s, a.env, "tenants", {"tenant_ids": sorted(set(ids + [str(t.tenant_id)]))}, True)
    print(f"tenant id: {t.tenant_id}")
    return 0


def cmd_load_docs(a) -> int:
    from .retrieval.embedders import by_name
    from .retrieval.ingest import plan_ingest, read_docs

    tid = _tenant(a.tenant)
    if tid == DEMO_TENANT_ID:
        print("Use python -m frontdesk.demo_tenant for the demo tenant.", file=sys.stderr)
        return 2
    docs = read_docs(sorted(pathlib.Path(a.dir).glob("*.md")), uri_prefix=f"client://{a.tenant}/")
    execute(_db(a), plan_ingest(tid, docs, by_name(a.embedder), approved_by=a.approved_by, is_demo_content=False), write=a.write)
    return 0


def cmd_create_staff(a) -> int:
    """With --dsn (a laptop replay) only the staff row is written, so handoffs name a real person;
    in AWS the Cognito user is created too, and MFA is set up at first sign-in."""
    tid = _tenant(a.tenant)
    topics = [t.strip() for t in a.topics.split(",") if t.strip()]
    if not a.dsn and not a.pool:
        print("--pool is required in AWS (the Cognito user pool id)", file=sys.stderr)
        return 2

    def precondition(tx):
        if tx.one("SELECT 1 AS x FROM app.staff WHERE email = :e", {"e": a.email}):
            return "a staff member with this e-mail already exists"
        return None if tx.one("SELECT 1 AS x FROM app.tenants") else "tenant not found"

    def apply(tx):
        sub = None
        if not a.dsn:
            idp = _session(a.profile).client("cognito-idp")
            user = idp.admin_create_user(UserPoolId=a.pool, Username=a.email, DesiredDeliveryMediums=["EMAIL"], UserAttributes=[
                {"Name": "email", "Value": a.email}, {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": a.name}, {"Name": "custom:tenant_id", "Value": str(tid)}])
            idp.admin_add_user_to_group(UserPoolId=a.pool, Username=a.email, GroupName=a.role)
            sub = next(x["Value"] for x in user["User"]["Attributes"] if x["Name"] == "sub")
        tx.execute("""INSERT INTO app.staff (tenant_id, display_name, email, role, cognito_sub, handles_topics)
                      VALUES (app.current_tenant(), :n, :e, :r, :s, ARRAY(SELECT jsonb_array_elements_text(:t::jsonb)))""",
                   {"n": a.name, "e": a.email, "r": a.role, "s": sub, "t": topics})
        return {"staff": a.email, "cognito_sub": sub or "none (local)", "topics": topics}

    where = "staff row only (local replay)" if a.dsn else f"Cognito user in {a.pool} (MFA set up at first sign-in)"
    execute(_db(a), WritePlan(f"create {a.role} {a.name} <{a.email}>", tid, [where, f"handles: {', '.join(topics) or 'anything not routed elsewhere'}"],
                              precondition, apply), write=a.write)
    return 0


def cmd_map_whatsapp(a) -> int:
    tid = _tenant(a.tenant)
    _ssm_put(_session(a.profile), a.env, f"routing/whatsapp/{a.phone_number_id}", {"tenant_id": str(tid)}, a.write)

    def apply(tx):
        tx.execute("""INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), 'whatsapp', :v::jsonb, 'admin')
                      ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                   {"v": {"phone_number_id": a.phone_number_id}})
        return {"mapped": a.phone_number_id}

    execute(_db(a), WritePlan("store the tenant's WhatsApp number id", tid, [a.phone_number_id], apply=apply), write=a.write)
    return 0


def cmd_create_widget_key(a) -> int:
    key = routing.new_widget_key()
    _ssm_put(_session(a.profile), a.env, f"routing/widget/{routing.key_hash(key)}", {"tenant_id": str(_tenant(a.tenant)), "origins": a.origin}, a.write)
    if a.write:
        print(f"widget key (public; put it in the site's <script> tag): {key}")
    return 0


def cmd_block_sender(a) -> int:
    from . import consent

    tid = _tenant(a.tenant)

    def precondition(tx):
        return None if tx.one("SELECT 1 AS x FROM app.customers WHERE channel_address = :n", {"n": a.number}) else "no customer with that number"

    def apply(tx):
        cid = tx.scalar("SELECT customer_id::text FROM app.customers WHERE channel_address = :n", {"n": a.number})
        consent.record(tx, cid, "service", False, "staff", "blocked by staff for abuse")
        return {"blocked_customer": cid}

    execute(_db(a), WritePlan(f"stop answering {a.number}", tid, ["service consent set to false (staff block)"], precondition, apply), write=a.write)
    return 0


def cmd_start_experiment(a) -> int:
    from . import experiments

    execute(_db(a), experiments.start_plan(_tenant(a.tenant), pathlib.Path(a.prereg), a.approved_by), write=a.write)
    return 0


def cmd_stop_experiment(a) -> int:
    from . import experiments

    execute(_db(a), experiments.stop_plan(_tenant(a.tenant), a.id, a.reason), write=a.write)
    return 0


def cmd_pause_assistant(a) -> int:
    from . import controls

    plan = (controls.pause_plan if a.cmd == "pause-assistant" else controls.resume_plan)(_tenant(a.tenant), a.reason, a.by)
    execute(_db(a), plan, write=a.write)
    return 0


def cmd_deactivate_staff(a) -> int:
    from . import staff

    def cognito(email: str) -> None:
        idp = _session(a.profile).client("cognito-idp")
        idp.admin_user_global_sign_out(UserPoolId=a.pool, Username=email)
        idp.admin_disable_user(UserPoolId=a.pool, Username=email)

    execute(_db(a), staff.deactivate_plan(_tenant(a.tenant), a.email, a.reason, a.by, cognito), write=a.write)
    return 0


def cmd_connect_orders_api(a) -> int:
    """The shared secret is generated straight into SSM SecureString and never printed or stored
    in the database; the client's developer reads it once over a secure channel (DEPLOY.md)."""
    import secrets

    from . import audit

    tid = _tenant(a.tenant)
    if not a.base_url.startswith("https://"):
        print("The client's API must use HTTPS.", file=sys.stderr)
        return 2
    name = f"/frontdesk/{a.env}/orders-api/{tid}/secret"

    def precondition(tx):
        return None if tx.one("SELECT 1 AS x FROM app.tenants") else "tenant not found"

    def apply(tx):
        _session(a.profile).client("ssm").put_parameter(Name=name, Value=secrets.token_urlsafe(32), Type="SecureString", Overwrite=True)
        tx.execute("""INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), 'orders_api', :v::jsonb, :b)
                      ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, approved_by = EXCLUDED.approved_by, updated_at = now()""",
                   {"v": {"base_url": a.base_url, "secret_parameter": name}, "b": a.by})
        audit.record(tx, actor_type="operator", actor_id=a.by, action="write", resource_type="integration",
                     resource_id="orders_api", detail={"base_url": a.base_url})
        return {"connected": a.base_url, "secret_parameter": name}

    execute(_db(a), WritePlan(f"connect the order API at {a.base_url}", tid,
                              [f"new shared secret in SSM SecureString {name}", "tenant config orders_api",
                               "run the conformance kit against their sandbox before this (docs/engagement/integration-contract.md)"],
                              precondition, apply), write=a.write)
    return 0


def cmd_offboard_export(a) -> int:
    from . import offboarding

    m = offboarding.export(_db(a), _tenant(a.tenant), pathlib.Path(a.out), a.by)
    print(json.dumps(m["counts"]))
    print(f"exported to {a.out}; manifest sha256 {m['manifest_sha256']} (quote it to offboard-erase)")
    print("The folder holds personal information: hand it over encrypted, then delete your copy.")
    return 0


def cmd_offboard_erase(a) -> int:
    from . import offboarding

    db, tid = _db(a), _tenant(a.tenant)
    with db.transaction(tid) as tx:
        emails = [r["email"] for r in tx.execute("SELECT email FROM app.staff WHERE email NOT LIKE 'erased-%'")]
        name = (tx.one("SELECT name FROM app.tenants") or {"name": str(tid)})["name"]
        manifest_row = tx.one("""SELECT detail FROM app.audit_log WHERE action = 'export' AND resource_type = 'tenant'
                                 AND detail->>'manifest_sha256' = :h""", {"h": a.manifest_sha256})
    report = execute(db, offboarding.erase_plan(tid, a.manifest_sha256, a.by), write=a.write)
    if report is None:
        return 0
    idp = _session(a.profile).client("cognito-idp")
    for e in emails:
        try:
            idp.admin_delete_user(UserPoolId=a.pool, Username=e)
        except idp.exceptions.UserNotFoundException:
            pass
    detail = manifest_row["detail"] if manifest_row else {}
    manifest = {"counts": {k: detail.get(k, 0) for k in ("conversations", "documents", "consents", "handoffs")}, "exported_at": "see manifest.json"}
    out = pathlib.Path(a.certificate)
    out.write_text(offboarding.certificate(name, manifest, report, a.by))
    print(f"Cognito users removed: {len(emails)}. Certificate for the client: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # The shared options live on every subcommand, so they go after its name, as every command
    # in the docs writes them (`admin create-staff --profile fd-dev ...`). On the top-level
    # parser only, argparse refused them there. test_admin_cli.py parses every documented line.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile")
    common.add_argument("--env", default="dev")
    common.add_argument("--dsn", help="local Postgres instead of the Data API (replays and drills on a laptop)")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = lambda name: sub.add_parser(name, parents=[common])
    p = add("create-tenant"); p.add_argument("--name", required=True); p.add_argument("--slug", required=True)
    p = add("load-docs"); p.add_argument("--tenant", required=True); p.add_argument("--dir", required=True)
    p.add_argument("--approved-by"); p.add_argument("--embedder", default="cohere-embed-multilingual-v3")
    p = add("create-staff"); p.add_argument("--pool", help="Cognito user pool id (not needed with --dsn)"); p.add_argument("--tenant", required=True)
    p.add_argument("--email", required=True); p.add_argument("--name", required=True); p.add_argument("--role", choices=["owner", "agent"], required=True)
    p.add_argument("--topics", default="", help="comma-separated topics this person handles, e.g. negotiation,booking")
    p = add("map-whatsapp"); p.add_argument("--tenant", required=True); p.add_argument("--phone-number-id", required=True)
    p = add("create-widget-key"); p.add_argument("--tenant", required=True); p.add_argument("--origin", action="append", required=True)
    p = add("block-sender"); p.add_argument("--tenant", required=True); p.add_argument("--number", required=True)
    p = add("start-experiment"); p.add_argument("--tenant", required=True); p.add_argument("--prereg", required=True)
    p.add_argument("--approved-by", required=True)
    p = add("stop-experiment"); p.add_argument("--tenant", required=True); p.add_argument("--id", required=True)
    p.add_argument("--reason", required=True)
    p = add("connect-orders-api"); p.add_argument("--tenant", required=True); p.add_argument("--base-url", required=True)
    p.add_argument("--by", required=True)
    p = add("offboard-export"); p.add_argument("--tenant", required=True); p.add_argument("--out", required=True)
    p.add_argument("--by", required=True)
    p = add("offboard-erase"); p.add_argument("--tenant", required=True); p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--pool", required=True); p.add_argument("--by", required=True); p.add_argument("--certificate", default="erasure-certificate.md")
    p = add("deactivate-staff"); p.add_argument("--tenant", required=True); p.add_argument("--email", required=True)
    p.add_argument("--pool", required=True); p.add_argument("--reason", required=True); p.add_argument("--by", required=True)
    for name in ("pause-assistant", "resume-assistant"):
        p = add(name); p.add_argument("--tenant", required=True); p.add_argument("--reason", required=True)
        p.add_argument("--by", required=True, help="your name, for the audit log")
    for sp in sub.choices.values():
        add_write_flags(sp)
    return ap


COMMANDS = {"create-tenant": cmd_create_tenant, "load-docs": cmd_load_docs, "create-staff": cmd_create_staff,
            "map-whatsapp": cmd_map_whatsapp, "create-widget-key": cmd_create_widget_key, "block-sender": cmd_block_sender,
            "start-experiment": cmd_start_experiment, "stop-experiment": cmd_stop_experiment,
            "pause-assistant": cmd_pause_assistant, "resume-assistant": cmd_pause_assistant,
            "deactivate-staff": cmd_deactivate_staff, "connect-orders-api": cmd_connect_orders_api,
            "offboard-export": cmd_offboard_export, "offboard-erase": cmd_offboard_erase}


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return COMMANDS[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
