"""Monthly cost estimate for Front Desk, computed only from the pricing snapshot.

Every assumption about volume is a named number below, so a reviewer can change
one and see the rand total move. The exchange rate is an assumption too: AWS
bills in USD, and the rand moves. Re-run fetch_prices.py before quoting a client.

Run:  python tools/pricing/cost_model.py   (writes docs/evidence/cost-estimate.md)
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs" / "evidence" / "pricing-snapshot.json"
OUT = ROOT / "docs" / "evidence" / "cost-estimate.md"

ZAR_PER_USD = 18.50  # assumption, not fetched: check the day's rate before quoting
BUDGET_ZAR = 300.0

# Volumes per scenario. "demo" is the month you show the product to prospects;
# "pilot" is one client on a second WhatsApp number at the volume the proposal's
# demo story quotes (about 250 conversations a month). Pilot costs are passed
# through to the client, which is why they are allowed to exceed the R300 cap.
SCENARIOS: dict[str, dict[str, float]] = {
    "idle": dict(
        answers=0, whatsapp_msgs_out=0, staff_mau=1, aurora_acu_hours=2, db_gb=1,
        s3_gb=1, log_gb=0.2, kms_keys=1, secrets=2, alarms=8, custom_metrics=4,
        sonnet_share=0.0, lambda_invocations=2_000, athena_tb=0.0,
    ),
    "demo": dict(
        answers=600, whatsapp_msgs_out=300, staff_mau=3, aurora_acu_hours=30, db_gb=1,
        s3_gb=2, log_gb=1, kms_keys=1, secrets=2, alarms=10, custom_metrics=6,
        sonnet_share=0.15, lambda_invocations=20_000, athena_tb=0.001,
    ),
    "pilot": dict(
        answers=1_500, whatsapp_msgs_out=2_000, staff_mau=5, aurora_acu_hours=200, db_gb=2,
        s3_gb=5, log_gb=3, kms_keys=2, secrets=4, alarms=12, custom_metrics=8,
        sonnet_share=0.15, lambda_invocations=80_000, athena_tb=0.005,
    ),
}

# Per-answer token budget (measured numbers replace these once Phase 2 logs them).
TOK_IN_FRESH = 1_700     # retrieved chunks + question + history
TOK_IN_CACHED = 900      # system prompt + tool schemas, served from the prompt cache
TOK_OUT = 250
EMBED_TOK_PER_QUERY = 60
GUARDRAIL_TEXT_UNITS_IN = 1      # prompt-attack + content filter on the question
GUARDRAIL_TEXT_UNITS_GROUNDING = 8  # grounding check covers sources + answer (1 unit = 1,000 chars)
LAMBDA_GB_S_PER_INVOCATION = 0.512 * 0.6  # 512 MB for ~600 ms, ARM


def load() -> dict[str, float]:
    snap = json.loads(SNAPSHOT.read_text())
    return {k: v["usd"] for k, v in snap["prices"].items()}


def estimate(p: dict[str, float], s: dict[str, float]) -> dict[str, float]:
    a = s["answers"]
    haiku = a * (1 - s["sonnet_share"])
    sonnet = a * s["sonnet_share"]
    llm = (
        haiku * (TOK_IN_FRESH * p["haiku55_in_mtok"] + TOK_IN_CACHED * p["haiku55_cache_read_mtok"] + TOK_OUT * p["haiku55_out_mtok"])
        + sonnet * (TOK_IN_FRESH * p["sonnet55_in_mtok"] + TOK_IN_CACHED * p["sonnet55_cache_read_mtok"] + TOK_OUT * p["sonnet55_out_mtok"])
    ) / 1_000_000
    rows = {
        "Bedrock: Claude (routed Haiku/Sonnet)": llm,
        "Bedrock: embeddings (Cohere Embed Multilingual)": a * EMBED_TOK_PER_QUERY * p["cohere_embed_multi_mtok"] / 1_000_000,
        "Bedrock: rerank (Cohere Rerank 3.5)": a * p["cohere_rerank35_search_unit"],
        "Bedrock: Guardrails": a * (GUARDRAIL_TEXT_UNITS_IN * p["guardrail_content_ktu"] + GUARDRAIL_TEXT_UNITS_GROUNDING * p["guardrail_grounding_ktu"]),
        "Aurora Serverless v2 (ACU-hours, scales to zero)": s["aurora_acu_hours"] * p["aurora_sv2_acu_hr"],
        "Aurora storage": s["db_gb"] * p["aurora_storage_gb_mo"],
        "RDS Data API": (a * 12 + s["lambda_invocations"]) * p["rds_data_api_per_req"],
        "Lambda (ARM)": s["lambda_invocations"] * (p["lambda_req_arm"] + LAMBDA_GB_S_PER_INVOCATION * p["lambda_gb_s_arm"]),
        "API Gateway HTTP API": s["lambda_invocations"] * p["http_api_per_req"],
        "Step Functions Express": a * (p["sfn_express_req"] + 0.256 * 3 * p["sfn_express_gb_s"]),
        "S3 (documents, transcripts, analytics)": s["s3_gb"] * p["s3_storage_gb_mo"] + 5_000 * p["s3_put_per_req"],
        "KMS customer-managed keys": s["kms_keys"] * p["kms_key_mo"] + 20_000 * p["kms_req"],
        "Secrets Manager (Data API requires it: admin + app per env)": s["secrets"] * p["secrets_manager_secret_mo"],
        "Cognito (Lite tier, staff only)": s["staff_mau"] * p["cognito_lite_mau"],
        "CloudWatch logs, metrics, alarms": s["log_gb"] * (p["cw_logs_ingest_gb"] + p["cw_logs_store_gb_mo"]) + s["alarms"] * p["cw_alarm_mo"] + s["custom_metrics"] * p["cw_metric_mo"],
        "Athena": s["athena_tb"] * p["athena_tb_scanned"],
        "WhatsApp: AWS per-message fee (ZA)": s["whatsapp_msgs_out"] * p["whatsapp_aws_fee_za"],
        "WhatsApp: Meta service-message fee (ZA)": s["whatsapp_msgs_out"] * p["whatsapp_meta_service_za"],
    }
    return rows


def free_tier_note() -> str:
    return (
        "Free allowances are NOT subtracted. Since the July 2025 change, new AWS accounts get "
        "credits under the Free plan instead of the old 12-month free tier, and some always-free "
        "allowances (for example Lambda requests, KMS requests, Cognito Lite MAUs) still apply. "
        "Treating everything as billable keeps the estimate on the safe side."
    )


def render() -> str:
    p = load()
    snap = json.loads(SNAPSHOT.read_text())
    dates = sorted({v["publicationDate"] for v in snap["prices"].values() if v.get("publicationDate")})
    lines = [
        "# Cost estimate (generated)",
        "",
        f"Generated by `tools/pricing/cost_model.py` from `docs/evidence/pricing-snapshot.json` "
        f"(AWS Price List API, offer publication dates {dates[0]} to {dates[-1]}). "
        f"Exchange rate assumed: R{ZAR_PER_USD:.2f} per USD. Do not edit by hand.",
        "",
        free_tier_note(),
        "",
    ]
    totals = {}
    est = {name: estimate(p, s) for name, s in SCENARIOS.items()}
    header = "| Line item | " + " | ".join(f"{n} (USD)" for n in SCENARIOS) + " |"
    lines += [header, "|" + "---|" * (len(SCENARIOS) + 1)]
    for item in next(iter(est.values())):
        lines.append(f"| {item} | " + " | ".join(f"{est[n][item]:.2f}" for n in SCENARIOS) + " |")
    for n in SCENARIOS:
        totals[n] = sum(est[n].values())
    lines.append("| **Total USD** | " + " | ".join(f"**{totals[n]:.2f}**" for n in SCENARIOS) + " |")
    lines.append("| **Total ZAR** | " + " | ".join(f"**R{totals[n] * ZAR_PER_USD:,.0f}**" for n in SCENARIOS) + " |")
    lines += ["", "Volumes per scenario:", ""]
    for n, s in SCENARIOS.items():
        lines.append(f"- **{n}**: " + ", ".join(f"{k}={v:g}" for k, v in s.items()))
    lines += [
        "",
        f"Budget check: idle and demo must stay under R{BUDGET_ZAR:.0f}. "
        + ", ".join(f"{n} = R{totals[n] * ZAR_PER_USD:,.0f} ({'within' if totals[n] * ZAR_PER_USD <= BUDGET_ZAR else 'OVER'})" for n in SCENARIOS)
        + ". Pilot costs are passed through to the paying client.",
        "",
        "The largest swing is Aurora ACU-hours: every message that wakes a paused database "
        "keeps it running for at least the auto-pause delay (5 minutes minimum). Steady daytime "
        "traffic keeps it awake, which is why the pilot scenario assumes 200 ACU-hours.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    OUT.write_text(render())
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
