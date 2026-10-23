"""The Data API adapter sets the tenant context inside the same transaction as the queries."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from frontdesk.db.dataapi import DataApiDatabase, to_sql_parameters


class FakeRdsData:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def begin_transaction(self, **kw):
        self.calls.append(("begin", kw))
        return {"transactionId": "tx-1"}

    def execute_statement(self, **kw):
        self.calls.append(("exec", kw))
        if self.fail_on and self.fail_on in kw["sql"]:
            raise RuntimeError("boom")
        return {"formattedRecords": json.dumps([{"n": 1}])}

    def commit_transaction(self, **kw):
        self.calls.append(("commit", kw))

    def rollback_transaction(self, **kw):
        self.calls.append(("rollback", kw))


def test_the_tenant_is_set_first_and_in_the_same_transaction_as_every_query():
    fake = FakeRdsData()
    db = DataApiDatabase("arn:cluster", "arn:secret", "frontdesk", client=fake)
    tid = uuid.uuid4()
    with db.transaction(tid) as tx:
        assert tx.execute("SELECT 1 AS n") == [{"n": 1}]
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["begin", "exec", "exec", "commit"]
    first = fake.calls[1][1]
    assert "set_config('app.tenant_id'" in first["sql"]
    assert first["parameters"][0]["value"]["stringValue"] == str(tid)
    assert all(c[1].get("transactionId") == "tx-1" for c in fake.calls[1:])


def test_a_failed_statement_rolls_the_transaction_back():
    fake = FakeRdsData(fail_on="explode")
    db = DataApiDatabase("arn:cluster", "arn:secret", "frontdesk", client=fake)
    with pytest.raises(RuntimeError):
        with db.transaction(uuid.uuid4()) as tx:
            tx.execute("SELECT explode()")
    assert fake.calls[-1][0] == "rollback"


def test_parameters_are_typed_for_the_data_api():
    u = uuid.uuid4()
    p = {x["name"]: x for x in to_sql_parameters({
        "u": u, "n": 3, "f": 1.5, "b": True, "none": None, "j": {"a": 1},
        "t": dt.datetime(2026, 10, 22, 8, 0, tzinfo=dt.UTC), "s": "x"})}
    assert p["u"]["typeHint"] == "UUID" and p["u"]["value"]["stringValue"] == str(u)
    assert p["n"]["value"] == {"longValue": 3}
    assert p["b"]["value"] == {"booleanValue": True}
    assert p["none"]["value"] == {"isNull": True}
    assert p["j"]["typeHint"] == "JSON"
    assert p["t"]["typeHint"] == "TIMESTAMP" and p["t"]["value"]["stringValue"].startswith("2026-10-22 08:00:00")


def test_a_malformed_tenant_id_is_refused_before_any_call():
    fake = FakeRdsData()
    db = DataApiDatabase("arn:cluster", "arn:secret", "frontdesk", client=fake)
    with pytest.raises(ValueError):
        with db.transaction("'; DROP TABLE x; --"):
            pass


class Resuming(Exception):
    """The shape of botocore's ClientError for a paused Aurora Serverless v2 cluster."""

    response = {"Error": {"Code": "DatabaseResumingException", "Message": "The Aurora DB instance is resuming"}}


class PausedRdsData(FakeRdsData):
    def __init__(self, wakes_after: int):
        super().__init__()
        self.wakes_after = wakes_after

    def begin_transaction(self, **kw):
        if self.wakes_after > 0:
            self.wakes_after -= 1
            raise Resuming()
        return super().begin_transaction(**kw)


def test_a_paused_database_is_waited_for_instead_of_failing_the_first_message(monkeypatch):
    from frontdesk.db import dataapi

    slept = []
    monkeypatch.setattr(dataapi, "_sleep", slept.append)
    db = DataApiDatabase("arn:cluster", "arn:secret", "frontdesk", client=PausedRdsData(wakes_after=3))
    with db.transaction(uuid.uuid4()) as tx:
        assert tx.execute("SELECT 1 AS n") == [{"n": 1}]
    assert slept == [1, 2, 4]


def test_waiting_for_a_resume_stops_inside_the_api_gateway_time_limit(monkeypatch):
    from frontdesk.db import dataapi

    slept = []
    monkeypatch.setattr(dataapi, "_sleep", slept.append)
    db = DataApiDatabase("arn:cluster", "arn:secret", "frontdesk", client=PausedRdsData(wakes_after=99))
    with pytest.raises(Resuming):
        with db.transaction(uuid.uuid4()):
            pass
    assert sum(slept) < 29


def test_other_errors_are_not_retried(monkeypatch):
    from frontdesk.db import dataapi

    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException"}}

    class DeniedRdsData(FakeRdsData):
        def begin_transaction(self, **kw):
            raise Denied()

    slept = []
    monkeypatch.setattr(dataapi, "_sleep", slept.append)
    with pytest.raises(Denied):
        with DataApiDatabase("arn:cluster", "arn:secret", "frontdesk", client=DeniedRdsData()).transaction(uuid.uuid4()):
            pass
    assert slept == []
