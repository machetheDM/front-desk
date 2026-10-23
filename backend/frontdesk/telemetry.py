"""Metrics and security events, without customer text.

Metrics use CloudWatch Embedded Metric Format: one JSON line on stdout, which Lambda turns
into a metric with no API call. Security events go to EventBridge (detections DET-05, DET-08,
DET-11). Locally both are captured in memory so tests can assert on them.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

CAPTURED: list[dict] = []  # tests read this


def metric(name: str, value: float = 1.0, unit: str = "Count", **dims: str) -> None:
    env = os.environ.get("FRONTDESK_ENV", "local")
    dimensions = {"Env": env, **dims}
    line = {"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": "FrontDesk", "Dimensions": [list(dimensions)], "Metrics": [{"Name": name, "Unit": unit}]}]},
        name: value, **dimensions}
    CAPTURED.append(line)
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        print(json.dumps(line))


def security_event(detail_type: str, detail: dict[str, Any]) -> None:
    detail = {"env": os.environ.get("FRONTDESK_ENV", "local"), **detail}
    CAPTURED.append({"event": detail_type, **detail})
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        import boto3

        boto3.client("events").put_events(Entries=[{"Source": "frontdesk.app", "DetailType": detail_type, "Detail": json.dumps(detail)}])
