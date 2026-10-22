"""The labelled question set (evals/datasets/*.yaml)."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "evals" / "datasets" / "bushveld.synthetic.yaml"
HOLDOUT = ROOT / "evals" / "datasets" / "bushveld-holdout.synthetic.yaml"


@dataclass
class Gold:
    doc: str
    heading: str
    contains: str | None = None


@dataclass
class Item:
    id: str
    text: str
    split: str
    lang: str
    intent: str
    action: str
    reason: str | None = None
    gold: list[Gold] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    tool: str | None = None
    channel_address: str | None = None
    small_talk: bool = False

    @property
    def answerable(self) -> bool:
        return self.action == "answer"

    @property
    def retrieval_scored(self) -> bool:
        return bool(self.gold)


def load(path: pathlib.Path = DEFAULT, split: str | None = None) -> list[Item]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = []
    for r in raw:
        gold = [Gold(g["doc"], g["heading"], g.get("contains")) for g in r.get("gold", [])]
        items.append(Item(r["id"], r["text"], r["split"], r["lang"], r["intent"], r["action"], r.get("reason"), gold,
                          r.get("facts", []), r.get("forbidden", []), r.get("tool"), r.get("channel_address"), r.get("small_talk", False)))
    return [i for i in items if split in (None, "all") or i.split == split]
