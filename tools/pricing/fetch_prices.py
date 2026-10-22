"""Pull the exact AWS prices Front Desk depends on from the AWS Price List API.

Why this exists: the cost table in docs/ARCHITECTURE.md must not be typed from
memory. AWS changed the free tier in 2025 and prices move. This script reads the
public Price List API (no AWS credentials needed), picks the specific usage types
we rely on, and writes a dated snapshot to docs/evidence/pricing-snapshot.json.
The cost model (cost_model.py) only ever reads that snapshot, so every rand in
the cost table traces back to an AWS-published SKU.

Run:  python tools/pricing/fetch_prices.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
from dataclasses import dataclass

BASE = "https://pricing.us-east-1.amazonaws.com"
HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / ".cache"
DEFAULT_OUT = HERE.parents[1] / "docs" / "evidence" / "pricing-snapshot.json"


@dataclass(frozen=True)
class Query:
    key: str
    offer: str
    region: str
    usagetype: str
    model: str | None = None  # Bedrock offers share usage types across models
    desc_contains: str | None = None
    begin_range: str | None = "0"  # first pricing tier unless stated


QUERIES: list[Query] = [
    # --- Home region af-south-1 ---
    Query("aurora_sv2_acu_hr", "AmazonRDS", "af-south-1", "AFS1-Aurora:ServerlessV2Usage"),
    Query("aurora_storage_gb_mo", "AmazonRDS", "af-south-1", "AFS1-Aurora:StorageUsage"),
    Query("aurora_io_per_req", "AmazonRDS", "af-south-1", "AFS1-Aurora:StorageIOUsage"),
    Query("rds_data_api_per_req", "AmazonRDS", "af-south-1", "AFS1-RDS:Data-API-Usage", desc_contains="Aurora PostgreSQL Data API request up to 1B"),
    Query("lambda_req_arm", "AWSLambda", "af-south-1", "AFS1-Request-ARM"),
    Query("lambda_gb_s_arm", "AWSLambda", "af-south-1", "AFS1-Lambda-GB-Second-ARM"),
    Query("s3_storage_gb_mo", "AmazonS3", "af-south-1", "AFS1-TimedStorage-ByteHrs"),
    Query("s3_put_per_req", "AmazonS3", "af-south-1", "AFS1-Requests-Tier1"),
    Query("s3_get_per_req", "AmazonS3", "af-south-1", "AFS1-Requests-Tier2"),
    Query("kms_key_mo", "awskms", "af-south-1", "af-south-1-KMS-Keys"),
    Query("kms_req", "awskms", "af-south-1", "af-south-1-KMS-Requests"),
    Query("http_api_per_req", "AmazonApiGateway", "af-south-1", "AFS1-ApiGatewayHttpRequest"),
    Query("sfn_express_req", "AmazonStates", "af-south-1", "AFS1-StepFunctions-Request"),
    Query("sfn_express_gb_s", "AmazonStates", "af-south-1", "AFS1-StepFunctions-GB-Second"),
    Query("cognito_lite_mau", "AmazonCognito", "af-south-1", "AFS1-CognitoLiteMAU"),
    Query("cognito_essentials_mau", "AmazonCognito", "af-south-1", "AFS1-CognitoEssentialsMAU"),
    Query("cw_logs_ingest_gb", "AmazonCloudWatch", "af-south-1", "AFS1-DataProcessing-Bytes"),
    Query("cw_logs_store_gb_mo", "AmazonCloudWatch", "af-south-1", "AFS1-TimedStorage-ByteHrs"),
    Query("cw_alarm_mo", "AmazonCloudWatch", "af-south-1", "AFS1-CW:AlarmMonitorUsage"),
    Query("cw_metric_mo", "AmazonCloudWatch", "af-south-1", "AFS1-CW:MetricMonitorUsage"),
    Query("secrets_manager_secret_mo", "AWSSecretsManager", "af-south-1", "AFS1-AWSSecretsManager-Secrets"),
    Query("athena_tb_scanned", "AmazonAthena", "af-south-1", "AFS1-DataScannedInTB"),
    Query("cloudtrail_paid_event", "AWSCloudTrail", "af-south-1", "AFS1-PaidEventsRecorded"),
    Query("whatsapp_aws_fee_za", "AmazonPinpoint", "af-south-1", "AFS1-WhatsApp-ZA-MessageFee-Standard"),
    Query("whatsapp_meta_service_za", "AWSEndUserMessaging3pFees", "af-south-1", "AFS1-WhatsApp-ZA-ConversationFee-Service"),
    Query("whatsapp_meta_utility_za", "AWSEndUserMessaging3pFees", "af-south-1", "AFS1-WhatsApp-ZA-ConversationFee-Utility"),
    Query("whatsapp_meta_marketing_za", "AWSEndUserMessaging3pFees", "af-south-1", "AFS1-WhatsApp-ZA-ConversationFee-Marketing"),
    # "When a client pays" services: priced so the decision is informed, not switched on.
    Query("guardduty_ct_event", "AmazonGuardDuty", "af-south-1", "AFS1-PaidEventsAnalyzed"),
    Query("securityhub_check", "AWSSecurityHub", "af-south-1", "AFS1-PaidComplianceCheck"),
    Query("config_item", "AWSConfig", "af-south-1", "AFS1-ConfigurationItemRecorded"),
    Query("waf_webacl_mo", "awswaf", "af-south-1", "AFS1-WebACL"),
    Query("waf_rule_mo", "awswaf", "af-south-1", "AFS1-Rule"),
    Query("waf_req", "awswaf", "af-south-1", "AFS1-Request"),
    # --- Language region eu-central-1 (see ADR 0002) ---
    Query("haiku55_in_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_input_tokens_standard-Units", model="Claude Haiku 5.5"),
    Query("haiku55_out_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_output_tokens_standard-Units", model="Claude Haiku 5.5"),
    Query("haiku55_cache_read_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_cache_read_tokens_standard-Units", model="Claude Haiku 5.5"),
    Query("haiku55_cache_write_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_cache_write_tokens_standard-Units", model="Claude Haiku 5.5"),
    Query("sonnet55_in_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_input_tokens_standard-Units", model="Claude Sonnet 5.5"),
    Query("sonnet55_out_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_output_tokens_standard-Units", model="Claude Sonnet 5.5"),
    Query("sonnet55_cache_read_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_cache_read_tokens_standard-Units", model="Claude Sonnet 5.5"),
    Query("cohere_embed_multi_mtok", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_InputTokenCount-Units", model="Embed Multilingual"),
    Query("cohere_rerank35_search_unit", "AmazonBedrockFoundationModels", "eu-central-1", "EUC1-MP:EUC1_search_units-Units", model="Rerank 3.5"),
    Query("titan_embed_v2_ktok", "AmazonBedrock", "eu-central-1", "EUC1-TitanEmbeddingV2-Text-input-tokens"),
    Query("amazon_rerank_search_unit", "AmazonBedrock", "eu-central-1", "EUC1-AmazonRerank-v1-searchunits"),
    Query("guardrail_content_ktu", "AmazonBedrock", "eu-central-1", "EUC1-Guardrail-ContentPolicyUnitsConsumed"),
    Query("guardrail_grounding_ktu", "AmazonBedrock", "eu-central-1", "EUC1-Guardrail-ContextualGroundingPolicyUnitsConsumed"),
    Query("guardrail_pii_paid_ktu", "AmazonBedrock", "eu-central-1", "EUC1-Guardrail-SensitiveInformationPolicyPaidUnitsConsumed"),
    # --- For comparison only: what af-south-1 offers for the same models (Global routing only) ---
    Query("afs1_haiku55_global_in_mtok", "AmazonBedrockFoundationModels", "af-south-1", "AFS1-MP:AFS1_input_tokens_global_standard-Units", model="Claude Haiku 5.5"),
]


def _get(url: str) -> dict:
    CACHE.mkdir(exist_ok=True)
    name = url.replace("/", "_").replace(":", "")[-180:]
    path = CACHE / name
    if not path.exists():
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - fixed AWS host
            path.write_bytes(r.read())
    return json.loads(path.read_text())


def offer_for(offer: str, region: str) -> dict:
    idx = _get(f"{BASE}/offers/v1.0/aws/{offer}/current/region_index.json")
    regions = idx["regions"]
    if region not in regions:
        raise LookupError(f"{offer} publishes no prices for {region}")
    return _get(BASE + regions[region]["currentVersionUrl"])


def resolve(q: Query) -> dict:
    data = offer_for(q.offer, q.region)
    terms = data.get("terms", {}).get("OnDemand", {})
    for sku, product in data["products"].items():
        attrs = product["attributes"]
        if attrs.get("usagetype") != q.usagetype:
            continue
        if q.model and attrs.get("model") != q.model:
            continue
        for term in terms.get(sku, {}).values():
            for dim in term["priceDimensions"].values():
                if q.desc_contains and q.desc_contains not in dim["description"]:
                    continue
                if q.begin_range is not None and dim.get("beginRange", "0") != q.begin_range:
                    continue
                return {
                    "key": q.key,
                    "offer": q.offer,
                    "region": q.region,
                    "usagetype": q.usagetype,
                    "model": q.model,
                    "usd": float(dim["pricePerUnit"]["USD"]),
                    "unit": dim["unit"],
                    "description": dim["description"],
                    "sku": sku,
                    "publicationDate": data.get("publicationDate"),
                }
    raise LookupError(f"No price found for {q}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    rows, missing = [], []
    for q in QUERIES:
        try:
            rows.append(resolve(q))
        except LookupError as e:
            missing.append(str(e))
    out = {"source": BASE, "prices": {r["key"]: r for r in rows}, "missing": missing}
    pathlib.Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} prices to {args.out}; {len(missing)} missing")
    for m in missing:
        print("  MISSING:", m, file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
