"""The owner report: what the replay shows, in words an owner reads in five minutes."""

from __future__ import annotations

import collections
import statistics

from .stats import fmt_rate

REASON_LABEL = {
    "unanswerable": "Your documents don't answer it", "human_requested": "Customer asked for a person",
    "negotiation": "Discount or price negotiation", "booking_commitment": "Wants a date booked or a visit",
    "complaint": "Complaint", "fault": "System fault reported", "refund": "Refund request",
    "out_of_scope": "Outside your business", "injection": "Manipulation attempt (refused)",
    "verification_needed": "Order question from an unmatched number", "validation_failed": "Draft failed checks",
}


def owner_report(rows, *, tenant_name: str, pipeline: str) -> str:
    n = len(rows)
    answered = [r for r in rows if r.outcome.action == "answer"]
    handed = [r for r in rows if r.outcome.action in ("handoff", "degraded")]
    refused = [r for r in rows if r.outcome.action == "refused"]
    after = [r for r in rows if r.after_hours]
    after_answered = [r for r in after if r.outcome.action == "answer"]
    reasons = collections.Counter(r.outcome.handoff_reason for r in handed + refused)
    staff = collections.Counter((r.outcome.assigned_staff or {}).get("display_name", "unassigned") for r in handed)
    intents = collections.Counter(r.outcome.intent for r in rows)
    gaps = [r.outcome.redacted.text for r in handed if r.outcome.handoff_reason == "unanswerable"]
    lat = [r.outcome.latency_ms for r in rows]
    redacted = sum(1 for r in rows if r.outcome.redacted.kinds)

    lines = [
        f"# Replay report: {tenant_name}",
        "",
        f"Pipeline: **{pipeline}**. {'Offline baseline (no AI model): treat these numbers as a floor.' if pipeline == 'offline' else 'Production pipeline (Bedrock).'}",
        "Nothing was sent to any customer. Personal details were removed before processing.",
        "",
        "## The headline",
        "",
        f"- Questions replayed: **{n}**",
        f"- Would have been answered from your documents: **{fmt_rate(len(answered), n)}**",
        f"- Would have gone to a named staff member: {fmt_rate(len(handed), n)}",
        f"- Refused as manipulation attempts: {len(refused)}",
        f"- Arrived after hours: {fmt_rate(len(after), n)}; of those answered at once: {fmt_rate(len(after_answered), len(after)) if after else 'n/a'}",
        f"- Messages containing personal details that were redacted: {redacted}",
        f"- Median time to a reply in this run: {statistics.median(lat):.0f} ms" if lat else "",
        "",
        "## Why questions would go to your staff",
        "",
        "| Reason | Count |",
        "|---|---|",
        *[f"| {REASON_LABEL.get(k or '', k)} | {v} |" for k, v in reasons.most_common()],
        "",
        "Who would receive them: " + ", ".join(f"{k} ({v})" for k, v in staff.most_common()) + ".",
        "",
        "## What customers ask about",
        "",
        "| Topic | Questions |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in intents.most_common()],
        "",
        "## Questions your documents do not answer",
        "",
        "These are the cheapest wins: one new FAQ entry each.",
        "",
        *[f"- {g}" for g in gaps[:15]],
        "",
        "## Sample answers (with the document each one quoted)",
        "",
    ]
    for r in answered[:8]:
        cite = "; ".join(c.quote[:80] for c in r.outcome.citations) or "(tool lookup)"
        lines += [f"**Q:** {r.outcome.redacted.text}", "", f"**A:** {r.outcome.reply}", "", f"_Source:_ {cite}", ""]
    lines += [
        "## How to read this",
        "",
        "Ranges are 95% confidence intervals. The assistant never invents a price, date or policy: if your",
        "documents do not say it, the question goes to a person. Answers quote your approved documents only.",
        "",
    ]
    return "\n".join(x for x in lines if x is not None)
