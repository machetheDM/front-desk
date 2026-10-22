"""A new table without an RLS policy is a failing migration."""

from __future__ import annotations

import pathlib
import shutil

import pytest

from frontdesk.db.lint_migrations import lint_dir, lint_text
from frontdesk.db.migrate import MIGRATIONS, MigrationError, apply
from frontdesk.db.sqlsplit import split_sql

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bad_migrations"


def test_the_real_migrations_pass_the_linter():
    assert lint_dir(MIGRATIONS) == []


def test_a_table_without_rls_fails_the_linter_with_every_missing_piece_named():
    findings = lint_dir(FIXTURES)
    problems = {f.problem for f in findings if f.table == "invoices"}
    assert "row-level security not enabled" in problems
    assert any("not forced" in p for p in problems)
    assert "no RLS policy" in problems


def test_enabling_rls_without_forcing_it_still_fails():
    sql = """CREATE TABLE app.x (tenant_id uuid NOT NULL, id int);
             ALTER TABLE app.x ENABLE ROW LEVEL SECURITY;
             CREATE POLICY p ON app.x USING (tenant_id = app.current_tenant());"""
    assert [f.problem for f in lint_text("t.sql", sql)] == ["row-level security not forced (owner would bypass it)"]


def test_a_table_without_tenant_id_fails():
    sql = """CREATE TABLE app.x (id int);
             ALTER TABLE app.x ENABLE ROW LEVEL SECURITY; ALTER TABLE app.x FORCE ROW LEVEL SECURITY;
             CREATE POLICY p ON app.x USING (true);"""
    assert any("tenant_id" in f.problem for f in lint_text("t.sql", sql))


def test_turning_rls_off_later_fails():
    assert lint_text("t.sql", "ALTER TABLE app.messages DISABLE ROW LEVEL SECURITY;")
    assert lint_text("t.sql", "ALTER TABLE app.messages NO FORCE ROW LEVEL SECURITY;")


def test_granting_update_on_an_append_only_table_fails():
    assert lint_text("t.sql", "GRANT SELECT, UPDATE ON app.audit_log TO frontdesk_app;")


def test_granting_bypassrls_fails():
    assert lint_text("t.sql", "ALTER ROLE frontdesk_app BYPASSRLS;")


def test_the_splitter_keeps_dollar_quoted_function_bodies_whole():
    sql = "DO $$ BEGIN PERFORM 1; PERFORM 2; END $$; SELECT 'a;b'; -- c;d\nSELECT 3"
    assert split_sql(sql) == ["DO $$ BEGIN PERFORM 1; PERFORM 2; END $$", "SELECT 'a;b'", "SELECT 3"]


@pytest.mark.db
def test_the_migration_runner_refuses_a_table_without_rls_even_if_the_linter_were_skipped(admin_dsn, tmp_path):
    """Belt and braces: the catalog check after migrating catches what text linting could miss."""
    from frontdesk.db.migrate import local_runner

    run_tx, query = local_runner(admin_dsn)
    # Create the bad table directly (as if by some path the linter cannot parse), then run the
    # runner over an empty directory so only the catalog check stands between us and success.
    run_tx([("CREATE TABLE app.sneaky (tenant_id uuid NOT NULL, x int)", None)])
    try:
        empty = tmp_path / "migrations"
        empty.mkdir()
        with pytest.raises(MigrationError, match="app.sneaky"):
            apply(run_tx, query, empty)
    finally:
        run_tx([("DROP TABLE app.sneaky", None)])


@pytest.mark.db
def test_the_migration_runner_rejects_the_failing_example(admin_dsn, tmp_path):
    from frontdesk.db.migrate import local_runner

    d = tmp_path / "m"
    d.mkdir()
    shutil.copy(FIXTURES / "0099_table_without_rls.sql", d)
    run_tx, query = local_runner(admin_dsn)
    with pytest.raises(MigrationError, match="row-level security not enabled"):
        apply(run_tx, query, d)


@pytest.mark.db
def test_an_edited_migration_that_was_already_applied_is_refused(admin_dsn, tmp_path):
    from frontdesk.db.migrate import local_runner

    d = tmp_path / "m"
    shutil.copytree(MIGRATIONS, d)
    first = sorted(d.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- edited\n")
    run_tx, query = local_runner(admin_dsn)
    with pytest.raises(MigrationError, match="edited after it was applied"):
        apply(run_tx, query, d)
