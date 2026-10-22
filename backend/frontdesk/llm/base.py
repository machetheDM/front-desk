"""Interfaces shared by the offline and the Bedrock language models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..retrieval.search import Hit
from .schemas import AnswerDraft


class ModelUnavailable(RuntimeError):
    """Raised for any failure of the language layer. The agent turns it into DEGRADED."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.calls += other.calls


@dataclass
class DraftRequest:
    question: str              # redacted
    sources: list[Hit]
    tool_results: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)  # [{"role": "customer"|"assistant", "text": redacted}]
    customer_name: str | None = None
    tenant_name: str = ""
    assistant_name: str = ""
    language_hint: str = "en"
    known_entities: list[str] = field(default_factory=list)  # place names etc. from tenant config


@dataclass
class DraftResult:
    draft: AnswerDraft
    model_id: str
    usage: Usage
    latency_ms: float
    model_inputs: list[str] = field(default_factory=list)  # exactly what text was sent, for the PII metric


class LanguageModel(Protocol):
    name: str

    def draft(self, req: DraftRequest, *, large: bool = False, run_tool=None) -> DraftResult: ...

    def summarise(self, lines: list[str]) -> tuple[str, Usage]: ...
