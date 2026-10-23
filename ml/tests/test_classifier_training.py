"""Training the classifier: reproducible, leak-free, and served exactly as trained."""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

from frontdesk.agent import classifier as serving
from frontdesk_ml import dataset, training_data
from frontdesk_ml.classifier import fit, training_rows


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@pytest.fixture(scope="module")
def fitted():
    return fit(training_rows(), threshold=0.9)


def test_the_training_data_regenerates_identically_from_its_seed():
    committed = [json.loads(line) for line in training_data.OUT.read_text().splitlines()]
    assert training_data.generate() == committed


def test_no_evaluation_question_appears_in_the_training_data():
    # A classifier scored on questions it was trained on reports a number nobody should trust.
    train = {_norm(r["text"]) for r in training_rows()}
    evals = dataset.load() + dataset.load(dataset.HOLDOUT)
    leaked = [i.id for i in evals if _norm(i.text) in train]
    assert leaked == []


def test_the_numpy_serving_code_gives_the_same_probabilities_as_scikit_learn(fitted):
    models, export = fitted
    clf = serving.Classifier(export)
    texts = [i.text for i in dataset.load()[:40]]
    X = np.stack([serving.features(t) for t in texts])
    sk_np = models["needs_person"].predict_proba(X)[:, list(models["needs_person"].classes_).index(True)]
    sk_intent = models["intent"].predict(X)
    for t, p_sk, i_sk in zip(texts, sk_np, sk_intent):
        p = clf.predict(t)
        assert abs(p.needs_person - p_sk) < 1e-3, t
        assert p.intent == i_sk, t


def test_the_committed_weights_are_what_the_committed_training_data_produces(fitted):
    _, export = fitted
    fresh, committed = serving.Classifier(export), serving.default()
    for item in dataset.load(dataset.HOLDOUT):
        a, b = fresh.predict(item.text), committed.predict(item.text)
        assert abs(a.needs_person - b.needs_person) < 1e-3 and a.intent == b.intent, item.id
