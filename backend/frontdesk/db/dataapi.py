"""RDS Data API implementation (ADR 0004).

The database has no network path from the internet and Lambdas run outside the VPC; this
HTTPS API, authorised by IAM, is the only way in. Each `transaction()` maps to
BeginTransaction / ExecuteStatement... / CommitTransaction, so `set_config(..., true)` stays in
force for every statement of the transaction exactly as it does over a direct connection.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .base import Database, Transaction

# Aurora Serverless v2 at 0 ACU pauses when idle (ADR 0004), and the first Data API call then
# fails with DatabaseResumingException while it wakes, typically in about 15 seconds. Without
# waiting, the first customer message after a quiet night fails: the web chat shows an error
# and WhatsApp relies on retries. The waits add up to 23 s, inside API Gateway's 29 s limit.
RESUME_WAITS_S = (1, 2, 4, 8, 8)
_sleep = time.sleep


def call_resuming(fn: Callable[..., Any], **kwargs: Any) -> Any:
    for wait in (*RESUME_WAITS_S, None):
        try:
            return fn(**kwargs)
        except Exception as e:  # botocore's ClientError, matched by code so tests need no botocore stubs
            code = getattr(e, "response", {}).get("Error", {}).get("Code")
            if code != "DatabaseResumingException" or wait is None:
                raise
            _sleep(wait)
    raise AssertionError("unreachable")


def to_sql_parameters(params: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, v in (params or {}).items():
        p: dict[str, Any] = {"name": name}
        if v is None:
            p["value"] = {"isNull": True}
        elif isinstance(v, bool):
            p["value"] = {"booleanValue": v}
        elif isinstance(v, int):
            p["value"] = {"longValue": v}
        elif isinstance(v, float):
            p["value"] = {"doubleValue": v}
        elif isinstance(v, uuid.UUID):
            p["value"] = {"stringValue": str(v)}
            p["typeHint"] = "UUID"
        elif isinstance(v, dt.datetime):
            p["value"] = {"stringValue": v.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")}
            p["typeHint"] = "TIMESTAMP"
        elif isinstance(v, (dict, list)):
            p["value"] = {"stringValue": json.dumps(v)}
            p["typeHint"] = "JSON"
        else:
            p["value"] = {"stringValue": str(v)}
        out.append(p)
    return out


class _DataApiTx(Transaction):
    def __init__(self, db: DataApiDatabase, transaction_id: str) -> None:
        self.db = db
        self.transaction_id = transaction_id

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        resp = self.db.client.execute_statement(
            resourceArn=self.db.cluster_arn,
            secretArn=self.db.secret_arn,
            database=self.db.database,
            sql=sql,
            parameters=to_sql_parameters(params),
            transactionId=self.transaction_id,
            formatRecordsAs="JSON",
        )
        raw = resp.get("formattedRecords")
        return json.loads(raw) if raw else []


class DataApiDatabase(Database):
    def __init__(self, cluster_arn: str, secret_arn: str, database: str, client: Any | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("rds-data")
        self.client = client
        self.cluster_arn = cluster_arn
        self.secret_arn = secret_arn
        self.database = database

    def _begin(self) -> Transaction:
        # Only the first call of a transaction can meet a paused cluster: once it has begun, it is awake.
        resp = call_resuming(self.client.begin_transaction,
                             resourceArn=self.cluster_arn, secretArn=self.secret_arn, database=self.database)
        return _DataApiTx(self, resp["transactionId"])

    def _commit(self, tx: Transaction) -> None:
        assert isinstance(tx, _DataApiTx)
        self.client.commit_transaction(
            resourceArn=self.cluster_arn, secretArn=self.secret_arn, transactionId=tx.transaction_id
        )

    def _rollback(self, tx: Transaction) -> None:
        assert isinstance(tx, _DataApiTx)
        self.client.rollback_transaction(
            resourceArn=self.cluster_arn, secretArn=self.secret_arn, transactionId=tx.transaction_id
        )
