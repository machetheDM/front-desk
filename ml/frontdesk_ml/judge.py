"""Judging answers, and checking the judge against people.

Two judges share one interface:
- RubricJudge: correct iff every required fact is present and no forbidden fact is. Free,
  deterministic, blind to wording quality.
- BedrockJudge: Claude grades the answer against the reference facts and the cited sources.

A judge is only trusted after calibration: on answers that a person has also labelled
(evals/labels/human-judgments.yaml, written with `python -m frontdesk_ml.label`), we report
Cohen's kappa between judge and human. Kappa corrects raw agreement for chance: two raters who
both say "correct" 90% of the time agree 82% of the time by luck alone.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import yaml

from .metrics import facts_ok

ROOT = pathlib.Path(__file__).resolve().parents[2]
HUMAN_LABELS = ROOT / "evals" / "labels" / "human-judgments.yaml"
MIN_LABELS_FOR_KAPPA = 30


@dataclass(frozen=True)
class Judgement:
    correct: bool
    rationale: str = ""


class RubricJudge:
    name = "rubric-v1"

    def judge(self, question: str, reply: str, facts: list[str], forbidden: list[str], sources: list[str]) -> Judgement:
        ok = facts_ok(reply, facts, forbidden)
        return Judgement(ok, "all required facts present" if ok else "missing a required fact or contains a forbidden one")


class BedrockJudge:
    name = "bedrock-claude-judge-v1"
    PROMPT = ("You grade a customer-service answer. Reply with JSON only: {\"correct\": true|false, \"rationale\": \"...\"}.\n"
              "An answer is correct if it states every REQUIRED fact (wording may differ), states none of the FORBIDDEN "
              "facts, and every claim it makes is supported by the SOURCES. Ignore style.\n\n"
              "QUESTION: {q}\nREQUIRED: {facts}\nFORBIDDEN: {forbidden}\nSOURCES:\n{sources}\nANSWER: {a}")

    def __init__(self, model_id: str, client=None, region: str = "eu-central-1") -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self.client, self.model = client, model_id

    def judge(self, question, reply, facts, forbidden, sources) -> Judgement:
        prompt = self.PROMPT.format(q=question, facts=json.dumps(facts), forbidden=json.dumps(forbidden),
                                    sources="\n".join(sources)[:6000], a=reply)
        r = self.client.converse(modelId=self.model, messages=[{"role": "user", "content": [{"text": prompt}]}],
                                 inferenceConfig={"maxTokens": 200, "temperature": 0})
        text = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return Judgement(bool(data["correct"]), str(data.get("rationale", ""))[:300])


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Agreement between two binary raters, corrected for chance. 1 = perfect, 0 = chance."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty rating lists")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def load_human_labels(path: pathlib.Path = HUMAN_LABELS) -> dict[tuple[str, str], bool]:
    if not path.exists():
        return {}
    rows = yaml.safe_load(path.read_text()) or []
    return {(r["item_id"], r["pipeline"]): r["verdict"] == "correct" for r in rows}


def calibrate(judge_verdicts: dict[str, bool], pipeline: str, path: pathlib.Path = HUMAN_LABELS) -> dict:
    human = load_human_labels(path)
    pairs = [(judge_verdicts[i], h) for (i, p), h in human.items() if p == pipeline and i in judge_verdicts]
    if len(pairs) < MIN_LABELS_FOR_KAPPA:
        return {"kappa": None, "n": len(pairs), "status": f"needs at least {MIN_LABELS_FOR_KAPPA} human labels (have {len(pairs)}): run python -m frontdesk_ml.label"}
    a, b = zip(*pairs)
    return {"kappa": round(cohen_kappa(list(a), list(b)), 3), "n": len(pairs),
            "raw_agreement": round(sum(x == y for x, y in pairs) / len(pairs), 3), "status": "calibrated"}
