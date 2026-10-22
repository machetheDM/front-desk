"""Shared fixtures.

Database tests create a throwaway database, run the real migrations as the admin user, and
then connect as `frontdesk_app`, the same non-owner role production uses. Connecting as the
admin would make every isolation test pass for the wrong reason: superusers bypass RLS.
"""

from __future__ import annotations

import os
import uuid

import pytest

from frontdesk.db.local import LocalDatabase

ADMIN_DSN = os.environ.get("FRONTDESK_TEST_ADMIN_DSN", "postgresql://postgres@localhost:5433/postgres")
APP_PASSWORD = "test-only-not-a-secret"


def _admin_connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn, autocommit=True)


def _dsn_for(db: str, user: str, password: str | None = None) -> str:
    base = ADMIN_DSN.rsplit("/", 1)[0]
    scheme, rest = base.split("://", 1)
    host = rest.split("@", 1)[-1]
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"{scheme}://{auth}{host}/{db}"


@pytest.fixture(scope="session")
def database_name():
    try:
        conn = _admin_connect(ADMIN_DSN)
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"no test Postgres at {ADMIN_DSN}: {e}")
    name = f"frontdesk_test_{uuid.uuid4().hex[:8]}"
    conn.execute(f'CREATE DATABASE "{name}"')
    conn.close()
    from frontdesk.db.migrate import apply, local_runner

    admin_db_dsn = _dsn_for(name, ADMIN_DSN.split("://", 1)[1].split("@", 1)[0].split(":")[0])
    run_tx, query = local_runner(admin_db_dsn)
    apply(run_tx, query)
    with _admin_connect(admin_db_dsn) as c:
        c.execute(f"ALTER ROLE frontdesk_app PASSWORD '{APP_PASSWORD}'")
        c.execute(f'GRANT CONNECT ON DATABASE "{name}" TO frontdesk_app')
    yield name
    with _admin_connect(ADMIN_DSN) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def admin_dsn(database_name):
    user = ADMIN_DSN.split("://", 1)[1].split("@", 1)[0].split(":")[0]
    return _dsn_for(database_name, user)


@pytest.fixture(scope="session")
def app_dsn(database_name):
    return _dsn_for(database_name, "frontdesk_app", APP_PASSWORD)


@pytest.fixture()
def db(app_dsn):
    return LocalDatabase(app_dsn)


@pytest.fixture()
def two_tenants(db):
    from frontdesk.tenancy import create_tenant

    a = create_tenant(db, "Tenant A Hardware", f"a-{uuid.uuid4().hex[:6]}")
    b = create_tenant(db, "Tenant B Motors", f"b-{uuid.uuid4().hex[:6]}")
    return a, b
