"""Human-readable evaluation report (published per release as the model's scorecard)."""

from __future__ import annotations


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1%}" if isinstance(v, float) and v <= 1 else str(v)


def eval_report(r: dict) -> str:
    h, dev = r["headline"], r["splits"]["dev"]
    hold = r["splits"].get("holdout", {})
    rows = [
        ("Retrieval recall@5", "retrieval_recall_at_5"), ("Retrieval recall@1", "retrieval_recall_at_1"), ("Retrieval MRR", "retrieval_mrr"),
        ("Answer correctness", "answer_correctness"), ("Groundedness of sent answers", "groundedness"),
        ("Correct answer-or-handoff decision", "handoff_correct_rate"), ("Unanswerable questions handed over", "unanswerable_handed_over"),
        ("Forbidden facts in replies", "forbidden_fact_rate"), ("PII patterns reaching a model", "pii_reaching_model"),
        ("Share answered", "answered_share"),
    ]
    out = [f"# Evaluation report: {r['pipeline']} pipeline", "", f"Generated {r['generated_at']} from `{r['dataset']}`.",
           "Headline (gated) = **test split**. **Holdout** = questions written after tuning and never tuned on:",
           "the honest estimate (docs/MODEL-CARD.md). Dev = the split thresholds were tuned on.", "",
           "| Metric | Test (gated) | Holdout | Dev |", "|---|---|---|---|"]
    out += [f"| {label} | {_pct(h.get(k))} | {_pct(hold.get(k))} | {_pct(dev.get(k))} |" for label, k in rows]
    out += ["", f"Red-team attack success rate: **{_pct(h['redteam_attack_success_rate'])}** "
            f"({r['redteam']['succeeded']} of {r['redteam']['cases']} cases).", "",
            f"Judge calibration: {r['judge_calibration']['status']}"
            + (f", Cohen's kappa {r['judge_calibration']['kappa']} on {r['judge_calibration']['n']} items." if r['judge_calibration'].get('kappa') is not None else "."),
            "", "## By language (dev and test splits)", "", "| Language | n | recall@5 | correctness | decision |", "|---|---|---|---|---|"]
    for lang, m in r["by_language"].items():
        out.append(f"| {lang} | {m['n']} | {_pct(m['retrieval_recall_at_5'])} | {_pct(m['answer_correctness'])} | {_pct(m['handoff_correct_rate'])} |")
    out += ["", "## Cost and latency by route (test split)", "", "| Route | Answers | USD per answer | p50 latency (ms) |", "|---|---|---|---|"]
    for k, v in h.get("by_route", {}).items():
        out.append(f"| {k} | {v['answers']} | {v['cost_usd_per_answer']:.6f} | {v['latency_ms_p50']} |")
    out += ["", "## Red team by OWASP LLM Top 10 category", "", "| Category | Cases | Succeeded |", "|---|---|---|"]
    for k, v in r["redteam"]["by_owasp"].items():
        out.append(f"| {k} | {v['cases']} | {v['succeeded']} |")
    out += ["", "## Red team by MITRE ATLAS technique", "", "| Technique | Cases | Succeeded |", "|---|---|---|"]
    for k, v in r["redteam"]["by_atlas"].items():
        out.append(f"| {k} | {v['cases']} | {v['succeeded']} |")
    failed = [x for x in r["redteam_results"] if x["succeeded"]]
    if failed:
        out += ["", "### Successful attacks", ""] + [f"- {x['id']} ({x['category']}): {', '.join(x['why'])}" for x in failed]
    return "\n".join(out) + "\n"
