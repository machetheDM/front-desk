"""Shared fixtures.

Database tests create a throwaway database, run the real migrations as the admin user, and
then connect as `frontdesk_app`, the same non-owner role production uses. Connecting as the
admin would make every isolation test pass for the wrong reason: superusers bypass RLS.
"""

from __future__ import annotations

import os
import uuid

import pytest
from psycopg import sql

from frontdesk.db.local import LocalDatabase

ADMIN_DSN = os.environ.get("FRONTDESK_TEST_ADMIN_DSN", "postgresql://postgres@localhost:5433/postgres")
APP_PASSWORD = "test-only-not-a-secret"


def _admin_connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn, autocommit=True)


def _dsn_for(db: str, user: str | None = None, password: str | None = None) -> str:
    """Same server as ADMIN_DSN, another database; keeps the admin's password unless a
    different user is given (CI's Postgres requires a password, a laptop's may not)."""
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(ADMIN_DSN)
    if user is None:
        netloc = u.netloc
    else:
        host = u.netloc.rsplit("@", 1)[-1]
        netloc = f"{user}:{password}@{host}" if password else f"{user}@{host}"
    return urlunsplit((u.scheme, netloc, f"/{db}", u.query, u.fragment))


@pytest.fixture(scope="session")
def database_name():
    try:
        conn = _admin_connect(ADMIN_DSN)
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"no test Postgres at {ADMIN_DSN}: {e}")
    name = f"frontdesk_test_{uuid.uuid4().hex[:8]}"
    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    conn.close()
    from frontdesk.db.migrate import apply, local_runner

    admin_db_dsn = _dsn_for(name)
    run_tx, query = local_runner(admin_db_dsn)
    apply(run_tx, query)
    with _admin_connect(admin_db_dsn) as c:
        c.execute(sql.SQL("ALTER ROLE frontdesk_app PASSWORD {}").format(sql.Literal(APP_PASSWORD)))
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO frontdesk_app").format(sql.Identifier(name)))
    yield name
    with _admin_connect(ADMIN_DSN) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def admin_dsn(database_name):
    return _dsn_for(database_name)


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


@pytest.fixture(scope="session")
def demo_db(app_dsn):
    """The demo tenant loaded once per test session with the offline embedder."""
    from frontdesk import demo_tenant
    from frontdesk.retrieval.embedders import HashingEmbedder

    db = LocalDatabase(app_dsn)
    demo_tenant.load(db, HashingEmbedder(), write=True)
    return db


@pytest.fixture()
def offline_agent():
    from frontdesk import services
    from frontdesk.agent.state_machine import Agent

    return Agent(services.build("offline"))


def ask(db, agent, text, **turn):
    from frontdesk.agent.state_machine import Turn
    from frontdesk.tenancy import DEMO_TENANT_ID

    with db.transaction(DEMO_TENANT_ID) as tx:
        return agent.run(tx, Turn(text=text, **turn))
