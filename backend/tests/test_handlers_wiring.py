"""Every Lambda handler named in the CDK app exists, and the web chat handler works end to end
against the local database (offline pipeline)."""

from __future__ import annotations

import importlib
import json
import pathlib
import re
import uuid

import pytest

APP_STACK = pathlib.Path(__file__).resolve().parents[2] / "infra" / "lib" / "workload" / "app-stack.ts"


def test_every_handler_string_in_the_cdk_app_resolves_to_a_function():
    handlers = re.findall(r"'(frontdesk\.handlers\.[a-z_.]+)'", APP_STACK.read_text())
    assert len(handlers) >= 10
    for h in handlers:
        mod, fn = h.rsplit(".", 1)
        assert callable(getattr(importlib.import_module(mod), fn)), h


@pytest.mark.db
def test_the_web_chat_handler_answers_a_visitor_from_an_allowed_origin(demo_db, app_dsn, monkeypatch):
    from frontdesk import routing
    from frontdesk.handlers import common, web_chat
    from frontdesk.tenancy import DEMO_TENANT_ID

    key = routing.new_widget_key()
    monkeypatch.setenv("FRONTDESK_ROUTING_JSON", json.dumps({f"routing/widget/{routing.key_hash(key)}": {
        "tenant_id": str(DEMO_TENANT_ID), "origins": ["https://www.bushveldsolar.example"]}}))
    monkeypatch.setenv("FRONTDESK_DSN", app_dsn)
    monkeypatch.setenv("FRONTDESK_PIPELINE", "offline")
    routing._local.cache_clear(); common.db.cache_clear(); common.deps.cache_clear()
    event = lambda origin: {"headers": {"origin": origin, "x-frontdesk-widget-key": key},
                            "body": json.dumps({"session": uuid.uuid4().hex, "message": "How much is the Home 5kW backup package?"})}
    ok = web_chat.handler(event("https://www.bushveldsolar.example"), None)
    body = json.loads(ok["body"])
    assert ok["statusCode"] == 200 and body["action"] == "answer" and "R62 900" in body["reply"]
    assert "automated assistant" in body["reply"]  # a new visitor's first reply discloses it
    assert ok["headers"]["access-control-allow-origin"] == "https://www.bushveldsolar.example"
    assert web_chat.handler(event("https://evil.example"), None)["statusCode"] == 403
    routing._local.cache_clear(); common.db.cache_clear(); common.deps.cache_clear()
