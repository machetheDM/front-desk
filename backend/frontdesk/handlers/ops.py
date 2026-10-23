"""Operations Lambdas: migrations, post-deploy smoke test, nightly analytics export."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

from .. import routing
from ..db.migrate import MigrationError, apply, data_api_runner


def migrate(event, context):
    """Run as the Aurora admin through the Data API, then set the app role's password from its
    own secret. The password never appears in SQL text or logs: it is passed as a parameter
    into a transaction-local setting and read back inside the DDL."""
    import boto3

    cluster, admin_secret = os.environ["FRONTDESK_DB_CLUSTER_ARN"], os.environ["FRONTDESK_DB_ADMIN_SECRET_ARN"]
    run_tx, query = data_api_runner(cluster, admin_secret, os.environ.get("FRONTDESK_DB_NAME", "frontdesk"))
    try:
        done = apply(run_tx, query)
    except MigrationError as e:
        return {"ok": False, "error": str(e)}
    pw = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["FRONTDESK_DB_SECRET_ARN"])["SecretString"])["password"]
    run_tx([
        ("SELECT set_config('frontdesk.app_pw', :p, true)", {"p": pw}),
        ("DO $$ BEGIN EXECUTE format('ALTER ROLE frontdesk_app WITH LOGIN PASSWORD %L', current_setting('frontdesk.app_pw')); END $$", None),
        ("GRANT CONNECT ON DATABASE frontdesk TO frontdesk_app", None),
    ])
    return {"ok": True, "applied": done, "message": "RLS catalog check passed"}


def smoke(event, context):
    """After each deploy: one known question through the real agent against the demo tenant.
    Nothing is sent to anyone."""
    from ..agent.state_machine import Agent, Turn
    from ..tenancy import DEMO_TENANT_ID
    from .common import db, deps

    with db().transaction(DEMO_TENANT_ID) as tx:
        o = Agent(deps()).run(tx, Turn(text="How much is the Home 5kW backup package?", channel="web"))
    ok = o.action == "answer" and "62 900" in o.reply.replace(" ", " ")
    return {"ok": ok, "action": o.action, "route": o.route, "latency_ms": round(o.latency_ms)}


EXPORT_SQL = """
SELECT e.event_id, e.conversation_id::text AS conversation_id, e.type, e.occurred_at,
       coalesce(e.payload->>'route', '') AS route, coalesce(e.payload->>'intent', '') AS intent,
       coalesce((e.payload->>'after_hours')::boolean, false) AS after_hours,
       nullif(e.payload->>'first_reply_seconds', '')::float AS first_reply_seconds
FROM app.conversation_events e WHERE e.occurred_at >= :since AND e.occurred_at < :until ORDER BY e.event_id
"""


def export_rows(db, tenant_id: uuid.UUID, since: dt.datetime, until: dt.datetime) -> list[dict]:
    with db.transaction(tenant_id) as tx:
        rows = tx.execute(EXPORT_SQL, {"since": since, "until": until})
    return [{**r, "tenant_id": str(tenant_id), "occurred_at": str(r["occurred_at"])} for r in rows]


def export_events(event, context):
    """Nightly: yesterday's events (ids, types, timings; no text) -> S3 staging -> Athena
    INSERT into the Iceberg table. One tenant at a time, each in its own RLS context."""
    import boto3

    from .common import db

    until = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    since = until - dt.timedelta(days=1)
    bucket, prefix = os.environ["FRONTDESK_ANALYTICS_BUCKET"], f"staging/events/{since:%Y-%m-%d}"
    s3 = boto3.client("s3")
    n = 0
    for tid in routing.tenant_ids():
        rows = export_rows(db(), tid, since, until)
        if rows:
            body = "\n".join(json.dumps(r) for r in rows).encode()
            s3.put_object(Bucket=bucket, Key=f"{prefix}/{tid}.jsonl", Body=body, ServerSideEncryption="aws:kms",
                          SSEKMSKeyId=os.environ.get("FRONTDESK_KMS_KEY_ARN"))
            n += len(rows)
    athena = boto3.client("athena")
    dbname, wg = os.environ["FRONTDESK_GLUE_DATABASE"], os.environ["FRONTDESK_ATHENA_WORKGROUP"]
    for q in (
        f"CREATE EXTERNAL TABLE IF NOT EXISTS {dbname}.events_staging (tenant_id string, event_id bigint, conversation_id string, "
        f"type string, route string, intent string, after_hours boolean, first_reply_seconds double, occurred_at string) "
        f"ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe' LOCATION 's3://{bucket}/staging/events/'",
        f"INSERT INTO {dbname}.conversation_events SELECT tenant_id, event_id, conversation_id, type, route, intent, after_hours, "
        f"first_reply_seconds, CAST(from_iso8601_timestamp(replace(occurred_at, ' ', 'T')) AS timestamp(6)) FROM {dbname}.events_staging "
        f"WHERE \"$path\" LIKE '%/{since:%Y-%m-%d}/%'",
    ):
        athena.start_query_execution(QueryString=q, WorkGroup=wg)  # nosemgrep: identifiers come from our own env, not input
    return {"exported": n, "day": f"{since:%Y-%m-%d}"}
