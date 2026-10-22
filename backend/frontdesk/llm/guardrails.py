"""Bedrock Guardrails as a separate, explicit check on input and output (layer 2 of 4 in
docs/AI-SECURITY.md). Called through ApplyGuardrail rather than attached to the model call, so
it also covers the structured tool-use output and can run grounding against our sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GuardrailResult:
    intervened: bool
    reasons: tuple[str, ...] = ()


class Guardrail(Protocol):
    def check_input(self, text: str) -> GuardrailResult: ...

    def check_output(self, answer: str, sources: str, query: str) -> GuardrailResult: ...


class NoGuardrail:
    """Offline pipeline: Bedrock Guardrails are not available without AWS. Our own screening
    (policy rules, redaction, validation) still runs; this layer is measured in the Bedrock
    evaluation only."""

    def check_input(self, text: str) -> GuardrailResult:
        return GuardrailResult(False)

    def check_output(self, answer: str, sources: str, query: str) -> GuardrailResult:
        return GuardrailResult(False)


class BedrockGuardrail:
    def __init__(self, guardrail_id: str, version: str, client=None, region: str = "eu-central-1") -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self.client, self.id, self.version = client, guardrail_id, version

    def _apply(self, source: str, content: list[dict]) -> GuardrailResult:
        r = self.client.apply_guardrail(guardrailIdentifier=self.id, guardrailVersion=self.version, source=source, content=content)
        if r.get("action") != "GUARDRAIL_INTERVENED":
            return GuardrailResult(False)
        reasons = []
        for a in r.get("assessments", []):
            reasons += [k for k in a.keys()]
        return GuardrailResult(True, tuple(sorted(set(reasons))))

    def check_input(self, text: str) -> GuardrailResult:
        return self._apply("INPUT", [{"text": {"text": text}}])

    def check_output(self, answer: str, sources: str, query: str) -> GuardrailResult:
        return self._apply("OUTPUT", [
            {"text": {"text": sources, "qualifiers": ["grounding_source"]}},
            {"text": {"text": query, "qualifiers": ["query"]}},
            {"text": {"text": answer, "qualifiers": ["guard_content"]}},
        ])
