"""Tenants, and the guard that keeps demo data out of real tenants."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .db import Database, Transaction

# The demo tenant id is fixed so that every environment, test and report refers to the same
# made-up company, and so that the guard below can recognise it without a database lookup.
DEMO_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-00000000d3e0")


class DemoDataLeak(RuntimeError):
    """Raised when demo content is about to be written into a real tenant."""


@dataclass(frozen=True)
class Tenant:
    tenant_id: uuid.UUID
    name: str
    slug: str
    is_demo: bool
    assistant_name: str


def create_tenant(db: Database, name: str, slug: str, *, is_demo: bool = False,
                  tenant_id: uuid.UUID | None = None, assistant_name: str | None = None) -> Tenant:
    tid = tenant_id or uuid.uuid4()
    if is_demo != (tid == DEMO_TENANT_ID):
        # One demo tenant, with one id. Anything else is a configuration mistake that could let
        # demo answers reach a real customer, so it fails loudly.
        raise DemoDataLeak("only DEMO_TENANT_ID may be a demo tenant, and it must be flagged as one")
    with db.transaction(tid) as tx:
        tx.execute(
            """INSERT INTO app.tenants (tenant_id, name, slug, is_demo, assistant_name)
               VALUES (:t, :n, :s, :d, :a)""",
            {"t": str(tid), "n": name, "s": slug, "d": is_demo, "a": assistant_name or f"{name} assistant"},
        )
    return Tenant(tid, name, slug, is_demo, assistant_name or f"{name} assistant")


def get_tenant(tx: Transaction) -> Tenant | None:
    row = tx.one("SELECT tenant_id, name, slug, is_demo, assistant_name FROM app.tenants")
    if row is None:
        return None
    return Tenant(uuid.UUID(str(row["tenant_id"])), row["name"], row["slug"], row["is_demo"], row["assistant_name"])


def assert_demo_content_allowed(target_tenant_id: uuid.UUID | str, content_is_demo: bool) -> None:
    """Call before writing any document or chunk. Demo content only ever goes to the demo tenant."""
    if content_is_demo and uuid.UUID(str(target_tenant_id)) != DEMO_TENANT_ID:
        raise DemoDataLeak(f"refusing to write demo content into tenant {target_tenant_id}")
