"""Run the full evaluation: retrieval, answers, handoffs, PII, red-team, judge calibration.

Creates a throwaway database, applies the real migrations, loads the demo tenant with the
pipeline's embedder, replays the labelled set through the real agent, and writes:

  <out>/metrics.json     headline numbers (test split) that the gate checks, plus dev and per-language
  <out>/eval-report.md   the same, for people
  <out>/replay.jsonl     every outcome, redacted (input for human labelling)

    FRONTDESK_TEST_ADMIN_DSN=postgresql://postgres@localhost:5433/postgres \\
      python -m frontdesk_ml.run_evals --pipeline offline --out reports/ci
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import os
import pathlib
import statistics
import uuid
from collections import defaultdict

from frontdesk import demo_tenant, services
from frontdesk.agent.state_machine import Agent, Turn
from frontdesk.db.local import LocalDatabase
from frontdesk.llm.metering import cost_usd
from frontdesk.llm.schemas import AnswerDraft
from frontdesk.llm.validate import validate
from frontdesk.redact import pii_leaks
from frontdesk.retrieval.search import hybrid_search
from frontdesk.tenancy import DEMO_TENANT_ID

from . import dataset, judge, metrics, redteam

FIXED_TIME = dt.datetime(2026, 10, 14, 21, 5, tzinfo=dt.timezone(dt.timedelta(hours=2)))  # a Wednesday evening


@contextlib.contextmanager
def scratch_database(admin_dsn: str):
    import psycopg
    from psycopg import sql

    from frontdesk.db.migrate import apply, local_runner

    name = f"frontdesk_eval_{uuid.uuid4().hex[:8]}"
    base, _ = admin_dsn.rsplit("/", 1)
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))  # nosemgrep -- (psycopg sql.Identifier/Literal composition, not string SQL)
    try:
        run_tx, query = local_runner(f"{base}/{name}")
        apply(run_tx, query)
        pw = uuid.uuid4().hex
        with psycopg.connect(f"{base}/{name}", autocommit=True) as c:
            c.execute(sql.SQL("ALTER ROLE frontdesk_app PASSWORD {}").format(sql.Literal(pw)))  # nosemgrep -- (psycopg sql.Identifier/Literal composition, not string SQL)
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO frontdesk_app").format(sql.Identifier(name)))  # nosemgrep -- (psycopg sql.Identifier/Literal composition, not string SQL)
        scheme, rest = base.split("://", 1)
        host = rest.split("@", 1)[-1]
        yield f"{scheme}://frontdesk_app:{pw}@{host}/{name}"
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))  # nosemgrep -- (psycopg sql.Identifier/Literal composition, not string SQL)


def doc_slugs(db) -> dict[str, str]:
    with db.transaction(DEMO_TENANT_ID) as tx:
        rows = tx.execute("SELECT document_id::text AS id, source_uri FROM app.documents")
    return {r["id"]: r["source_uri"].rsplit("/", 1)[-1].removesuffix(".md") for r in rows}


def evaluate(db, deps, items, pipeline: str) -> list[dict]:
    slugs = doc_slugs(db)
    agent, rubric = Agent(deps), judge.RubricJudge()
    rows = []
    for it in items:
        with db.transaction(DEMO_TENANT_ID) as tx:
            rank = None
            if it.retrieval_scored:
                hits = hybrid_search(tx, it.text, deps.embedder, k=10, reranker=deps.reranker)
                hd = [{"doc_slug": slugs.get(h.document_id, "?"), "heading": h.heading, "text": h.text} for h in hits]
                rank = metrics.first_gold_rank(hd, it.gold)
            o = agent.run(tx, Turn(text=it.text, channel="replay", channel_address=it.channel_address, received_at=FIXED_TIME))
        answered = metrics.decided_to_answer(o.action)
        # Correct means: it answered AND the answer states the facts. A handoff reply that happens
        # to mention office hours must not score as a correct answer about office hours.
        verdict = None
        if it.answerable:
            verdict = rubric.judge(it.text, o.reply, it.facts, it.forbidden, [h.text for h in o.retrieved]) if answered \
                else judge.Judgement(False, "handed over an answerable question")
        grounded = None
        if answered and not it.small_talk and not o.tool_calls:
            # Re-check the sent answer against the sources it cites: same rules as the live validator.
            grounded = bool(o.citations) and validate(AnswerDraft(action="answer", answer=o.reply, citations=o.citations), o.retrieved).ok
        rows.append({
            "id": it.id, "split": it.split, "lang": it.lang, "intent": it.intent, "label_action": it.action, "label_reason": it.reason,
            "action": o.action, "handoff_reason": o.handoff_reason, "answered": answered, "rank": rank,
            "correct": verdict.correct if verdict else None, "decision_correct": answered == it.answerable,
            "forbidden_hit": any(metrics.norm(f) in metrics.norm(o.reply) for f in it.forbidden),
            "grounded": grounded, "pii_in_model_inputs": sum(len(pii_leaks(x)) for x in o.model_inputs),
            "route": o.route, "model": o.model_id, "latency_ms": o.latency_ms, "usage": dataclasses.asdict(o.usage),
            "cost_usd": cost_usd(o.model_id, o.usage), "question_redacted": o.redacted.text, "reply": o.reply,
            "intent_pred": o.intent, "trace": o.trace, "problems": o.validation_problems,
        })
    return rows


def summarise(rows: list[dict]) -> dict:
    ranks = [r["rank"] for r in rows if r.get("_has_gold")]
    answerable = [r for r in rows if r["label_action"] == "answer"]
    unanswerable = [r for r in rows if r["label_reason"] == "unanswerable"]
    sent = [r for r in rows if r["grounded"] is not None]
    by_route = defaultdict(list)
    for r in rows:
        if r["model"]:
            by_route[r["route"]].append(r)
    return {
        "n": len(rows),
        "retrieval_recall_at_5": round(metrics.recall_at_k(ranks, 5), 4),
        "retrieval_recall_at_1": round(metrics.recall_at_k(ranks, 1), 4),
        "retrieval_mrr": round(metrics.mrr(ranks), 4),
        "answer_correctness": round(metrics.rate([bool(r["correct"]) for r in answerable]), 4),
        "groundedness": round(metrics.rate([bool(r["grounded"]) for r in sent]), 4) if sent else 1.0,
        "handoff_correct_rate": round(metrics.rate([r["decision_correct"] for r in rows]), 4),
        "unanswerable_handed_over": round(metrics.rate([not r["answered"] for r in unanswerable]), 4) if unanswerable else 1.0,
        "forbidden_fact_rate": round(metrics.rate([r["forbidden_hit"] for r in rows]), 4),
        "pii_reaching_model": sum(r["pii_in_model_inputs"] for r in rows),
        "answered_share": round(metrics.rate([r["answered"] for r in rows]), 4),
        "latency_ms_p50": round(statistics.median([r["latency_ms"] for r in rows]), 1) if rows else 0,
        "latency_ms_p95": round(sorted(r["latency_ms"] for r in rows)[int(0.95 * (len(rows) - 1))], 1) if rows else 0,
        "by_route": {k: {"answers": len(v), "cost_usd_per_answer": round(sum(x["cost_usd"] for x in v) / len(v), 6),
                         "latency_ms_p50": round(statistics.median(x["latency_ms"] for x in v), 1)} for k, v in by_route.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", choices=["offline", "bedrock"], default="offline")
    ap.add_argument("--out", default="reports/local")
    ap.add_argument("--dataset", default=str(dataset.DEFAULT))
    ap.add_argument("--holdout", default=str(dataset.HOLDOUT), help="clean holdout set, reported separately, never tuned on")
    a = ap.parse_args(argv)
    admin = os.environ.get("FRONTDESK_TEST_ADMIN_DSN", "postgresql://postgres@localhost:5433/postgres")
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    deps = services.build(a.pipeline)
    items = dataset.load(pathlib.Path(a.dataset))
    if a.holdout and pathlib.Path(a.holdout).exists():
        items += dataset.load(pathlib.Path(a.holdout))
    gold_ids = {i.id for i in items if i.retrieval_scored}
    with scratch_database(admin) as dsn:
        db = LocalDatabase(dsn)
        demo_tenant.load(db, deps.embedder, write=True)
        rows = evaluate(db, deps, items, a.pipeline)
        for r in rows:
            r["_has_gold"] = r["id"] in gold_ids
        rt_log = redteam.setup(db, deps.embedder)
        rt = redteam.run(db, deps, redteam.load_cases())
    splits = {s: summarise([r for r in rows if r["split"] == s]) for s in sorted({r["split"] for r in rows})}
    by_lang = {lang: summarise([r for r in rows if r["lang"] == lang and r["split"] != "holdout"]) for lang in sorted({r["lang"] for r in rows})}
    rubric_verdicts = {r["id"]: bool(r["correct"]) for r in rows if r["correct"] is not None}
    calib = judge.calibrate(rubric_verdicts, a.pipeline)
    headline = dict(splits["test"])
    headline["redteam_attack_success_rate"] = rt["attack_success_rate"]
    headline["pii_reaching_model"] = sum(v["pii_reaching_model"] for v in splits.values()) + sum(
        1 for r in rt["results"] if "PII reached the model" in r["why"])
    headline["judge_cohen_kappa"] = calib["kappa"]
    result = {"pipeline": a.pipeline, "generated_at": dt.datetime.now(dt.UTC).isoformat(), "dataset": str(pathlib.Path(a.dataset).resolve().relative_to(dataset.ROOT)) if pathlib.Path(a.dataset).resolve().is_relative_to(dataset.ROOT) else a.dataset,
              "headline": headline, "splits": splits, "by_language": by_lang, "redteam": {k: v for k, v in rt.items() if k != "results"},
              "redteam_results": rt["results"], "redteam_setup": rt_log, "judge_calibration": calib}
    (out / "metrics.json").write_text(json.dumps(result, indent=2, default=str))
    with (out / "replay.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, default=str) + "\n")
    from .report import eval_report

    (out / "eval-report.md").write_text(eval_report(result))
    print(json.dumps(headline, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
