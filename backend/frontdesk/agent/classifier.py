"""The trained handoff and intent classifier, served with numpy only.

Trained in ml/frontdesk_ml/classifier.py (scikit-learn logistic regression on the same
hashing features the offline embedder uses) and exported as plain weights to
data/classifier.v1.json, so the Lambda package needs no scikit-learn. Inference is a few
matrix-vector products: under a millisecond, and free, compared with a model call.

How it is used (agent/state_machine.py `_shadow`): in shadow mode. The rules decide, because
they are auditable and they beat this model on both evaluation splits
(docs/evidence/classifier-comparison.md). The model's prediction is stored next to the rules'
decision in every event payload, so the comparison can be repeated on real traffic after a
client's consented data retrains it (docs/MODEL-CARD.md section 6).
"""

from __future__ import annotations

import functools
import json
import pathlib
from dataclasses import dataclass

import numpy as np

from ..retrieval.embedders import HashingEmbedder
from ..retrieval.query_rewrite import rewrite

MODEL = pathlib.Path(__file__).resolve().parents[1] / "data" / "classifier.v1.json"


@dataclass(frozen=True)
class Prediction:
    needs_person: float
    reason: str
    intent: str
    intent_confidence: float


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def features(text: str) -> np.ndarray:
    return np.asarray(HashingEmbedder().embed_one(rewrite(text).expanded), dtype=np.float32)


class Classifier:
    def __init__(self, m: dict) -> None:
        self.version = m["version"]
        self.np_w = np.asarray(m["needs_person"]["coef"], dtype=np.float32)
        self.np_b = float(m["needs_person"]["intercept"])
        self.reason_classes = m["reason"]["classes"]
        self.reason_w = np.asarray(m["reason"]["coef"], dtype=np.float32)
        self.reason_b = np.asarray(m["reason"]["intercept"], dtype=np.float32)
        self.intent_classes = m["intent"]["classes"]
        self.intent_w = np.asarray(m["intent"]["coef"], dtype=np.float32)
        self.intent_b = np.asarray(m["intent"]["intercept"], dtype=np.float32)
        self.threshold = float(m.get("handoff_threshold", 0.85))

    @classmethod
    def load(cls, path: pathlib.Path = MODEL) -> Classifier:
        return cls(json.loads(path.read_text()))

    def predict(self, text: str) -> Prediction:
        x = features(text)
        p = 1 / (1 + np.exp(-(self.np_w @ x + self.np_b)))
        r = _softmax(self.reason_w @ x + self.reason_b)
        i = _softmax(self.intent_w @ x + self.intent_b)
        return Prediction(float(p), self.reason_classes[int(r.argmax())], self.intent_classes[int(i.argmax())], float(i.max()))


@functools.cache
def default() -> Classifier | None:
    return Classifier.load() if MODEL.exists() else None
