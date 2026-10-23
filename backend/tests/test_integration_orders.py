"""The client order-system integration: contract, conformance kit, and failure handling."""

from __future__ import annotations

import uuid

import pytest

from conftest import ask
from frontdesk import services
from frontdesk.agent.state_machine import Agent
from frontdesk.integrations import conformance
from frontdesk.integrations.orders import HttpOrdersAdapter
from frontdesk.tenancy import DEMO_TENANT_ID

SECRET = b"test-only-shared-secret"
ORDERS = {"ACM-2001": {"contact": "+27825550101", "status": "installed", "detail": "installed on 3 November, certificate e-mailed"}}


@pytest.fixture
def client_api():
    srv, url = conformance.reference_server(ORDERS, SECRET)
    yield url
    srv.shutdown()


def test_the_reference_implementation_passes_every_conformance_check(client_api):
    checks = conformance.run(client_api, SECRET, "ACM-2001", "+27825550101", allow_loopback_http=True)
    assert [(c.name, c.detail) for c in checks if not c.ok] == []
    assert len(checks) >= 8


def test_the_kit_fails_an_api_that_tells_anyone_the_order(client_api, monkeypatch):
    # A careless client API that skips the number check is exactly what the kit must catch.
    monkeypatch.setattr(conformance, "_same_number", lambda a, b: True)
    checks = {c.name: c.ok for c in conformance.run(client_api, SECRET, "ACM-2001", "+27825550101", allow_loopback_http=True)}
    assert checks["someone else's number is told nothing"] is False


def test_the_kit_fails_an_api_that_ignores_signatures(client_api):
    checks = {c.name: c.ok for c in conformance.run(client_api, b"a-different-secret", "ACM-2001", "+27825550101", allow_loopback_http=True)}
    assert checks["the order is told to the number on it"] is False


def test_client_apis_must_use_https_and_plain_http_is_allowed_only_to_loopback_in_tests():
    with pytest.raises(ValueError, match="HTTPS"):
        HttpOrdersAdapter("http://orders.example.co.za", SECRET)
    with pytest.raises(ValueError, match="HTTPS"):
        HttpOrdersAdapter("http://orders.example.co.za", SECRET, allow_loopback_http=True)
    HttpOrdersAdapter("http://127.0.0.1:9", SECRET, allow_loopback_http=True)


@pytest.mark.db
def test_a_tenant_with_an_orders_api_config_gets_the_http_adapter_with_its_ssm_secret(demo_db):
    names = []
    with demo_db.transaction(DEMO_TENANT_ID) as tx:
        tx.execute("""INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), 'orders_api', :v::jsonb, 'test')
                      ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value""",
                   {"v": {"base_url": "https://orders.example.co.za/frontdesk", "secret_parameter": "/frontdesk/dev/orders-api/demo/secret"}})
        try:
            adapter = services.orders_for(tx, secret=lambda n: names.append(n) or SECRET)
        finally:
            tx.execute("UPDATE app.tenant_config SET value = '{}'::jsonb WHERE key = 'orders_api'")
    assert isinstance(adapter, HttpOrdersAdapter) and names == ["/frontdesk/dev/orders-api/demo/secret"]


@pytest.mark.db
def test_when_the_clients_order_system_is_down_the_customer_is_handed_to_a_person_not_failed(demo_db):
    class Down:
        def status(self, ref, *, requester_address):
            raise TimeoutError("client API did not answer in 3 s")

    deps = services.build("offline")
    deps.orders, deps.orders_factory = Down(), None
    o = ask(demo_db, Agent(deps), "What is the status of my order BSE-1042?", channel_address=f"+2782{uuid.uuid4().int % 10**7:07d}")
    assert o.action == "handoff" and o.handoff_reason == "order_lookup_unavailable"
    assert "can't check orders right now" in o.reply and o.assigned_staff is not None
