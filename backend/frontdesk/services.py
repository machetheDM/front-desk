"""Builds the agent's dependencies for a pipeline: `offline` (CI, laptop) or `bedrock` (AWS)."""

from __future__ import annotations

import json
import os

from .agent import classifier
from .agent.state_machine import Deps
from .db import Transaction
from .integrations.orders import ConfigOrdersAdapter
from .llm import metering
from .retrieval.embedders import BedrockCohereEmbedder, HashingEmbedder
from .retrieval.rerankers import BedrockCohereReranker, LexicalReranker


def orders_for(tx: Transaction):
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'orders_demo'")
    if row:
        v = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
        return ConfigOrdersAdapter(v)
    return None  # SEAM(client-order-api): HttpOrdersAdapter from SSM when a client connects one


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
            model=BedrockClaude(os.environ.get("FRONTDESK_MODEL_SMALL", "eu.anthropic.claude-haiku-5-5"),
                                os.environ.get("FRONTDESK_MODEL_LARGE", "eu.anthropic.claude-sonnet-5-5"), region=region),
            embedder=BedrockCohereEmbedder(region=region),
            reranker=BedrockCohereReranker(region=region),
            guardrail=BedrockGuardrail(os.environ["FRONTDESK_GUARDRAIL_ID"], os.environ.get("FRONTDESK_GUARDRAIL_VERSION", "1"), region=region),
            over_cap=metering.over_cap if cap else None, orders_factory=orders_for, shadow=classifier.default(),
        )
    raise ValueError(f"unknown pipeline {pipeline}")
