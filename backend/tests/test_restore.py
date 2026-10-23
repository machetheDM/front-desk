"""Backups are only as good as the restore, and a restore is only safe with its protections."""

from __future__ import annotations

import datetime as dt
import os

import pytest

from conftest import APP_PASSWORD
from frontdesk import drills, pipeline, restore_check, services
from frontdesk.db.migrate import local_runner
from frontdesk.handlers.ops import export_rows
from frontdesk.tenancy import DEMO_TENANT_ID

pytestmark = pytest.mark.db


def test_a_correctly_migrated_database_passes_every_restore_check(demo_db, admin_dsn):
    rec = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "web", "web:restore-test", "What are your hours?", None))
    pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    _, query = local_runner(admin_dsn)
    checks = restore_check.admin_checks(query) + restore_check.app_checks(demo_db, [DEMO_TENANT_ID], dt.datetime.now(dt.UTC))
    assert [c["check"] for c in checks if not c["ok"]] == []
    assert any("minutes before the restore point" in c["detail"] for c in checks)


def test_the_backup_restore_drill_restores_a_safe_copy_and_catches_a_broken_one(demo_db, admin_dsn, tmp_path):
    try:
        drills._pg_tool("pg_dump")
    except FileNotFoundError:
        if os.environ.get("FRONTDESK_REQUIRE_PG_TOOLS"):
            raise  # CI sets this, so the drill can never silently stop running there
        pytest.skip("pg_dump not installed")
    steps = drills.backup_restore(admin_dsn, APP_PASSWORD, tmp_path)
    assert [(s.name, s.observed) for s in steps if not s.passed] == []
    assert len(steps) == 4


def test_the_analytics_export_carries_ids_and_decisions_but_no_text(demo_db):
    rec = pipeline.receive(demo_db, pipeline.Inbound(DEMO_TENANT_ID, "web", "web:export-test", "my number is 082 555 1234, how much is the 3kW?", "Test"))
    pipeline.answer(demo_db, services.build("offline"), DEMO_TENANT_ID, rec)
    now = dt.datetime.now(dt.UTC)
    rows = export_rows(demo_db, DEMO_TENANT_ID, now - dt.timedelta(minutes=5), now + dt.timedelta(minutes=1))
    mine = [r for r in rows if r["conversation_id"] == rec.conversation_id]
    assert {r["type"] for r in mine} == {"message_received", "answer_sent"}
    assert set(mine[0]) == {"event_id", "conversation_id", "type", "occurred_at", "route", "intent", "after_hours", "first_reply_seconds", "tenant_id"}
    assert "555" not in str(rows)
