"""Versioned prompts. The version and a content hash go into every answer event, so a change
in behaviour can be traced to the prompt that produced it."""

from __future__ import annotations

import functools
import hashlib
import pathlib
import os
from dataclasses import dataclass

CURRENT = "answer.v1"


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    sha: str

    def render(self, **kw: str) -> str:
        return self.text.format(**kw)


@functools.cache
def load(name: str = CURRENT) -> Prompt:
    text = (pathlib.Path(__file__).parent / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return Prompt(name, text, hashlib.sha256(text.encode()).hexdigest()[:12])


def canary() -> str:
    """A random-looking marker in the system prompt. If it shows up in output, the prompt
    leaked (DET-11). Rotated through SSM; the default is for local runs only."""
    return os.environ.get("FRONTDESK_PROMPT_CANARY", "fd-canary-7Q2M9X4K")
