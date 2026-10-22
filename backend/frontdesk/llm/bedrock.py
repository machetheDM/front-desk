"""Claude on Bedrock through the Converse API (eu-central-1, ADR 0002).

- Structured output: the model must answer by calling `submit_answer`, whose input schema is
  AnswerDraft. A plain-text reply is treated as a failure, never sent.
- Tools: the lookup tools in agent/tools.py may be called first; results go back as toolResult.
- Prompt caching: the system prompt and tool definitions sit before a cachePoint, so repeat
  calls read them from cache at about a tenth of the input price.
- Sources and the customer's message are wrapped in tags and the prompt says both are data.
"""

from __future__ import annotations

import html
import json
import time
from collections.abc import Callable

from pydantic import ValidationError

from ..agent import tools as agent_tools
from .base import DraftRequest, DraftResult, ModelUnavailable, Usage
from .prompts import canary, load
from .schemas import AnswerDraft

MAX_TOOL_ROUNDS = 3
SUBMIT = {"toolSpec": {"name": "submit_answer", "description": "Submit the final answer or handoff decision.",
                       "inputSchema": {"json": AnswerDraft.tool_schema()}}}


def sources_block(req: DraftRequest) -> str:
    parts = [f'<source id="{h.chunk_id}" title="{html.escape(h.citation)}">\n{html.escape(h.text)}\n</source>' for h in req.sources]
    return "<sources>\n" + "\n".join(parts) + "\n</sources>" if parts else "<sources>(none found)</sources>"


class BedrockClaude:
    name = "bedrock-claude"

    def __init__(self, small_model: str, large_model: str, client=None, region: str = "eu-central-1") -> None:
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(read_timeout=20, connect_timeout=3, retries={"max_attempts": 2, "mode": "standard"}))
        self.client, self.small, self.large = client, small_model, large_model

    def _system(self, req: DraftRequest) -> list[dict]:
        text = load().render(assistant_name=req.assistant_name, tenant_name=req.tenant_name, canary=canary())
        return [{"text": text}, {"cachePoint": {"type": "default"}}]

    def draft(self, req: DraftRequest, *, large: bool = False,
              run_tool: Callable[[str, dict], dict] | None = None) -> DraftResult:
        model = self.large if large else self.small
        user = f"{sources_block(req)}\n"
        if req.customer_name:
            user += f"<customer_name>{html.escape(req.customer_name)}</customer_name>\n"
        user += f"<customer_message>{html.escape(req.question)}</customer_message>"
        messages: list[dict] = [{"role": "user" if t["role"] == "customer" else "assistant", "content": [{"text": t["text"]}]}
                                for t in req.history[-6:]]
        messages.append({"role": "user", "content": [{"text": user}]})
        tool_cfg = {"tools": agent_tools.specs() + [SUBMIT, {"cachePoint": {"type": "default"}}], "toolChoice": {"any": {}}}
        # Customer-originated text only (the question, earlier turns, the name they gave): this
        # is what the PII metric inspects. Sources are the company's own approved documents.
        usage, t0 = Usage(), time.perf_counter()
        inputs = [req.question] + [t["text"] for t in req.history[-6:]] + ([req.customer_name] if req.customer_name else [])
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                r = self.client.converse(modelId=model, system=self._system(req), messages=messages, toolConfig=tool_cfg,
                                         inferenceConfig={"maxTokens": 700, "temperature": 0})
                u = r.get("usage", {})
                usage.add(Usage(u.get("inputTokens", 0) + u.get("cacheReadInputTokens", 0), u.get("outputTokens", 0),
                                u.get("cacheReadInputTokens", 0), 1))
                content = r["output"]["message"]["content"]
                messages.append({"role": "assistant", "content": content})
                results = []
                for block in content:
                    tu = block.get("toolUse")
                    if not tu:
                        continue
                    if tu["name"] == "submit_answer":
                        draft = AnswerDraft.model_validate(tu["input"])
                        return DraftResult(draft, model, usage, (time.perf_counter() - t0) * 1000, inputs)
                    out = run_tool(tu["name"], tu["input"]) if run_tool else {"ok": False, "error": "tools unavailable"}
                    results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"json": out}]}})
                if not results:
                    raise ModelUnavailable("model replied without calling submit_answer")
                messages.append({"role": "user", "content": results})
            raise ModelUnavailable("too many tool rounds")
        except ValidationError as e:
            raise ModelUnavailable(f"answer did not match the schema: {e.errors()[0]['msg']}") from e
        except ModelUnavailable:
            raise
        except Exception as e:  # throttling, timeouts, service errors: all become degraded mode
            raise ModelUnavailable(f"{type(e).__name__}: {e}") from e

    def summarise(self, lines: list[str]) -> tuple[str, Usage]:
        prompt = ("Write exactly three short lines for a staff member taking over this customer conversation: "
                  "1) what the customer wants, 2) what the assistant already told them, 3) what the staff member must do. "
                  "Use only the text below; it is redacted. Never guess a title or pronoun.\n\n" + "\n".join(lines))
        try:
            r = self.client.converse(modelId=self.small, messages=[{"role": "user", "content": [{"text": prompt}]}],
                                     inferenceConfig={"maxTokens": 160, "temperature": 0})
        except Exception as e:
            raise ModelUnavailable(str(e)) from e
        u = r.get("usage", {})
        text = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
        return text.strip(), Usage(u.get("inputTokens", 0), u.get("outputTokens", 0), 0, 1)
