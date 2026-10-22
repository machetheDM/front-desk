"""The structured output every answer must take. Free text from the model is never sent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HandoffReason = Literal[
    "human_requested", "negotiation", "unanswerable", "complaint", "booking_commitment",
    "refund", "out_of_scope", "injection", "fault", "validation_failed", "model_unavailable",
    "cost_cap", "verification_needed",
]


class Citation(BaseModel):
    chunk_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=300, description="Exact words from the source that support the answer")


class AnswerDraft(BaseModel):
    """What the model returns through the `submit_answer` tool."""

    action: Literal["answer", "handoff"]
    answer: str = Field(default="", max_length=1200, description="Reply to the customer; plain text, no links")
    citations: list[Citation] = Field(default_factory=list, max_length=4)
    handoff_reason: HandoffReason | None = None
    commitments: list[str] = Field(default_factory=list, description="Must be empty: the assistant makes no commitments")
    language: str = Field(default="en", max_length=12)

    @classmethod
    def tool_schema(cls) -> dict:
        schema = cls.model_json_schema()
        schema.pop("title", None)
        return schema
