"""Train the handoff/intent classifier and compare it with the rules (and the LLM).

    python -m frontdesk_ml.classifier train      # writes backend/frontdesk/data/classifier.v1.json
    python -m frontdesk_ml.classifier compare    # writes docs/evidence/classifier-comparison.md

Labels on the evaluation sets: "needs a person" means the right action is a handoff for a
reason a rule could know without the documents (asked for a person, discount, complaint,
fault, refund, booking, out of scope, injection). "Unanswerable" is not included: whether the
documents answer something is retrieval's job, not a text classifier's.
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, average_precision_score, f1_score, precision_score, recall_score, roc_auc_score

from frontdesk.agent import classifier as serving
from frontdesk.agent import policy

from . import dataset
from .training_data import OUT as TRAIN

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "classifier-comparison.md"
PERSON_REASONS = {"human_requested", "negotiation", "complaint", "fault", "refund", "booking_commitment", "out_of_scope", "injection"}


def fit(rows: list[dict], threshold: float = 0.85) -> tuple[dict, dict]:
    """The three logistic regressions, and the plain-weights export the Lambda serves."""
    X = np.stack([serving.features(r["text"]) for r in rows])
    fitted = {
        "needs_person": LogisticRegression(C=4.0, max_iter=2000, class_weight="balanced").fit(X, [r["needs_person"] for r in rows]),
        "reason": LogisticRegression(C=4.0, max_iter=2000).fit(X, [r["reason"] or "none" for r in rows]),
        "intent": LogisticRegression(C=4.0, max_iter=2000).fit(X, [r["intent"] for r in rows]),
    }
    r5 = lambda a: [round(float(v), 5) for v in a]
    m_np, m_reason, m_intent = fitted["needs_person"], fitted["reason"], fitted["intent"]
    export = {
        "version": "classifier.v1",
        "trained_on": str(TRAIN.relative_to(ROOT)), "n_train": len(rows),
        "features": "HashingEmbedder(rewrite(text).expanded), 1024-d",
        "handoff_threshold": threshold,
        "needs_person": {"coef": r5(m_np.coef_[0]), "intercept": float(m_np.intercept_[0])},
        "reason": {"classes": [str(c) for c in m_reason.classes_], "coef": [r5(c) for c in m_reason.coef_], "intercept": r5(m_reason.intercept_)},
        "intent": {"classes": [str(c) for c in m_intent.classes_], "coef": [r5(c) for c in m_intent.coef_], "intercept": r5(m_intent.intercept_)},
    }
    return fitted, export


def training_rows() -> list[dict]:
    return [json.loads(line) for line in TRAIN.read_text().splitlines()]


def train(threshold: float | None = None) -> dict:
    _, model = fit(training_rows(), threshold if threshold is not None else 0.85)
    serving.MODEL.write_text(json.dumps(model, separators=(",", ":")))
    serving.default.cache_clear()
    return model


def labels(items) -> tuple[list[bool], list[str]]:
    return [i.action == "handoff" and i.reason in PERSON_REASONS for i in items], [i.intent for i in items]


def score(name: str, items, needs, intents, ms: list[float], cost_per_1000: float) -> dict:
    y_np, y_int = labels(items)
    return {"system": name, "n": len(items),
            "needs_person_accuracy": accuracy_score(y_np, needs), "needs_person_precision": precision_score(y_np, needs, zero_division=0),
            "needs_person_recall": recall_score(y_np, needs, zero_division=0), "needs_person_f1": f1_score(y_np, needs, zero_division=0),
            "intent_accuracy": accuracy_score(y_int, intents), "intent_macro_f1": f1_score(y_int, intents, average="macro", zero_division=0),
            "latency_ms_p50": statistics.median(ms), "cost_usd_per_1000": cost_per_1000}


def run_rules(items):
    needs, intents, ms = [], [], []
    for i in items:
        t0 = time.perf_counter()
        needs.append(policy.evaluate(i.text).needs_person)
        intents.append(policy.keyword_intent(i.text))
        ms.append((time.perf_counter() - t0) * 1000)
    return needs, intents, ms


def run_model(items, clf, threshold):
    needs, intents, ms = [], [], []
    for i in items:
        t0 = time.perf_counter()
        p = clf.predict(i.text)
        needs.append(p.needs_person >= threshold)
        intents.append(p.intent)
        ms.append((time.perf_counter() - t0) * 1000)
    return needs, intents, ms


def run_combined(items, clf, threshold):
    """The option not shipped: rules first, the model adds a handoff only when the rules saw
    nothing. Intent stays with the rules (the model is worse at it on the test split)."""
    needs, intents, ms = [], [], []
    for i in items:
        t0 = time.perf_counter()
        rule = policy.evaluate(i.text).needs_person
        p = clf.predict(i.text)
        needs.append(rule or p.needs_person >= threshold)
        intents.append(policy.keyword_intent(i.text))
        ms.append((time.perf_counter() - t0) * 1000)
    return needs, intents, ms


def choose_threshold(dev) -> float:
    """On the dev split only: the lowest threshold whose combined system adds no false handoffs
    over the rules alone (precision first: a needless handoff costs staff time)."""
    clf = serving.default()
    base_fp = sum(1 for n, y in zip(run_rules(dev)[0], labels(dev)[0]) if n and not y)
    for t in (0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
        fp = sum(1 for n, y in zip(run_combined(dev, clf, t)[0], labels(dev)[0]) if n and not y)
        if fp <= base_fp:
            return t
    return 0.99


def where_rules_miss(items, clf) -> list[str]:
    """The only cases where a model could add anything: handoffs the rules did not catch,
    scored against the hardest true negatives the rules also let through."""
    y = labels(items)[0]
    passed = [(i, yy, clf.predict(i.text).needs_person) for i, yy in zip(items, y) if not policy.evaluate(i.text).needs_person]
    missed = [(i, p) for i, yy, p in passed if yy]
    top_neg = max((p for _, yy, p in passed if not yy), default=0.0)
    if not missed:
        return [f"Handoffs the rules missed: none (highest model score on a true non-handoff: {top_neg:.3f})."]
    rows = ", ".join(f"{i.id} ({i.reason}) {p:.3f}" for i, p in missed)
    above = sum(1 for _, p in missed if p > top_neg)
    return [f"Handoffs the rules missed: {len(missed)}; model scores {rows}. Highest model score on a true non-handoff: "
            f"{top_neg:.3f}, so {above} of {len(missed)} could be caught with no false handoff at a threshold chosen "
            "*on this split* (not allowed: thresholds come from dev)."]


def compare(llm=None) -> str:
    all_items = dataset.load() + dataset.load(dataset.HOLDOUT)
    dev = [i for i in all_items if i.split == "dev"]
    train()  # weights do not depend on the threshold; the threshold is then chosen on dev
    t = choose_threshold(dev)
    train(t)
    clf = serving.default()
    out = ["# Handoff and intent classifier: rules vs trained model vs LLM", "",
           f"Trained on `{clf.version}`: {TRAIN.relative_to(ROOT)} (synthetic, template-generated, disjoint from the evaluation sets).",
           f"Handoff threshold {t} chosen on the dev split (no added false handoffs over rules alone).",
           "Generated by `python -m frontdesk_ml.classifier compare`. Latency is per message on the CI runner class of machine.",
           "Decision: the rules act; the model runs in shadow mode (its prediction is stored in each event payload, `shadow`). "
           "Revisit when a model retrained on a client's consented data beats the rules on that client's holdout.", ""]
    for split in ("test", "holdout"):
        items = [i for i in all_items if i.split == split]
        rows = [score("rules (policy.py): what the agent runs", items, *run_rules(items), 0.0),
                score("trained model (numpy, in Lambda)", items, *run_model(items, clf, t), 0.0),
                score("rules + model as second opinion (not shipped)", items, *run_combined(items, clf, t), 0.0)]
        if llm is not None:
            rows.append(score("LLM (Claude Haiku via Bedrock)", items, *llm(items), llm.cost_per_1000))
        out += [f"## {split} split (n={len(items)})", "",
                "| System | Needs-person accuracy | Precision | Recall | F1 | Intent accuracy | Intent macro-F1 | p50 ms | USD per 1,000 |",
                "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['system']} | {r['needs_person_accuracy']:.2f} | {r['needs_person_precision']:.2f} | {r['needs_person_recall']:.2f} | "
                       f"{r['needs_person_f1']:.2f} | {r['intent_accuracy']:.2f} | {r['intent_macro_f1']:.2f} | {r['latency_ms_p50']:.2f} | {r['cost_usd_per_1000']:.4f} |")
        if llm is None:
            out.append("| LLM (Claude Haiku via Bedrock) | not yet measured: needs the dev account (`compare --llm`) | | | | | | | |")
        y = labels(items)[0]
        scores = [clf.predict(i.text).needs_person for i in items]
        out += ["", f"Model ranking quality without a threshold: ROC AUC {roc_auc_score(y, scores):.2f}, "
                    f"average precision {average_precision_score(y, scores):.2f} (base rate {sum(y) / len(y):.2f})."]
        out += ["", *where_rules_miss(items, clf)]
        out.append("")
    text = "\n".join(out) + "\n"
    EVIDENCE.write_text(text)
    return text


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "compare"
    if cmd == "train":
        m = train()
        print(f"trained {m['version']} on {m['n_train']} rows")
    elif cmd == "compare":
        llm = None
        if "--llm" in argv:
            from .llm_classifier import BedrockIntentClassifier

            llm = BedrockIntentClassifier()
        print(compare(llm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
