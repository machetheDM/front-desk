"""Refusal 6: demo data never reaches a real tenant."""

from __future__ import annotations

import pathlib
import uuid

import pytest

from frontdesk.retrieval.embedders import HashingEmbedder
from frontdesk.retrieval.ingest import plan_ingest, read_docs
from frontdesk.tenancy import DEMO_TENANT_ID, DemoDataLeak, assert_demo_content_allowed, create_tenant

DEMO_DOCS = pathlib.Path(__file__).resolve().parents[2] / "demo" / "bushveld" / "docs"


def test_planning_to_load_demo_content_into_a_real_tenant_is_refused():
    with pytest.raises(DemoDataLeak):
        plan_ingest(uuid.uuid4(), read_docs(sorted(DEMO_DOCS.glob("*.md"))), HashingEmbedder(), approved_by="x", is_demo_content=True)


def test_only_the_demo_tenant_id_may_hold_demo_content():
    assert_demo_content_allowed(DEMO_TENANT_ID, True)
    with pytest.raises(DemoDataLeak):
        assert_demo_content_allowed(uuid.uuid4(), True)


@pytest.mark.db
def test_a_real_tenant_cannot_be_created_with_the_demo_flag_or_the_demo_id(db):
    with pytest.raises(DemoDataLeak):
        create_tenant(db, "Fake", f"f-{uuid.uuid4().hex[:6]}", is_demo=True)
    with pytest.raises(DemoDataLeak):
        create_tenant(db, "Fake", f"f-{uuid.uuid4().hex[:6]}", tenant_id=DEMO_TENANT_ID, is_demo=False)


@pytest.mark.db
def test_demo_documents_are_not_visible_from_any_other_tenant(demo_db, two_tenants):
    a, _ = two_tenants
    with demo_db.transaction(a.tenant_id) as tx:
        assert tx.scalar("SELECT count(*) FROM app.chunks WHERE text ILIKE '%R62 900%'") == 0
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        assert tx.scalar("SELECT count(*) FROM app.chunks WHERE text ILIKE '%R62 900%'") >= 1
