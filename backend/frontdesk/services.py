"""Builds the agent's dependencies for a pipeline: `offline` (CI, laptop) or `bedrock` (AWS)."""

from __future__ import annotations

import json
import os

from .agent import classifier
from .agent.state_machine import Deps
from .db import Transaction
from .integrations.orders import ConfigOrdersAdapter, HttpOrdersAdapter
from .llm import metering
from .retrieval.embedders import BedrockCohereEmbedder, HashingEmbedder
from .retrieval.rerankers import BedrockCohereReranker, LexicalReranker


_SECRETS: dict[str, bytes] = {}


def _ssm_secret(name: str) -> bytes:
    """SecureString from SSM, cached for the life of the Lambda container. The value never
    goes to a log, an event or the database: only the parameter name is stored in config."""
    if name not in _SECRETS:
        import boto3

        _SECRETS[name] = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"].encode()
    return _SECRETS[name]


def _config(tx: Transaction, key: str) -> dict | None:
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = :k", {"k": key})
    if not row:
        return None
    return row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])


def orders_for(tx: Transaction, secret=_ssm_secret):
    """The tenant's order lookup: their own API when connected (admin connect-orders-api),
    the demo's made-up orders for the demo tenant, otherwise none (the tool says so)."""
    if api := _config(tx, "orders_api"):
        return HttpOrdersAdapter(api["base_url"], secret(api["secret_parameter"]))
    if demo := _config(tx, "orders_demo"):
        return ConfigOrdersAdapter(demo)
    return None


# The EU cross-Region inference profile (ADR 0002); infra/lib/build-app.ts sets the same default.
DEFAULT_MODEL_SMALL = "eu.anthropic.claude-haiku-5-5"


def build(pipeline: str = "offline", *, cap: bool = False) -> Deps:
    if pipeline == "offline":
        from .llm.offline import ExtractiveAnswerer

        return Deps(model=ExtractiveAnswerer(), embedder=HashingEmbedder(), reranker=LexicalReranker(),
                    over_cap=metering.over_cap if cap else None, orders_factory=orders_for, shadow=classifier.default())
    if pipeline == "bedrock":
        from .llm.bedrock import BedrockClaude
        from .llm.guardrails import BedrockGuardrail

        region = os.environ.get("FRONTDESK_AI_REGION", "eu-central-1")
        return Deps(
            model=BedrockClaude(os.environ.get("FRONTDESK_MODEL_SMALL", DEFAULT_MODEL_SMALL),
                                os.environ.get("FRONTDESK_MODEL_LARGE", "eu.anthropic.claude-sonnet-5-5"), region=region),
            embedder=BedrockCohereEmbedder(region=region),
            reranker=BedrockCohereReranker(region=region),
            guardrail=BedrockGuardrail(os.environ["FRONTDESK_GUARDRAIL_ID"], os.environ.get("FRONTDESK_GUARDRAIL_VERSION", "1"), region=region),
            over_cap=metering.over_cap if cap else None, orders_factory=orders_for, shadow=classifier.default(),
        )
    raise ValueError(f"unknown pipeline {pipeline}")
