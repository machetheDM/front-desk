"""Shared Lambda wiring, created once per container and reused across invocations."""

from __future__ import annotations

import functools
import json
import os
import uuid

from .. import services
from ..db import Database, connect


@functools.cache
def db() -> Database:
    return connect()


@functools.cache
def deps():
    return services.build(os.environ.get("FRONTDESK_PIPELINE", "bedrock"), cap=True)


def response(status: int, body: dict, origin: str | None = None) -> dict:
    headers = {"content-type": "application/json", "cache-control": "no-store", "x-content-type-options": "nosniff"}
    if origin:
        headers["access-control-allow-origin"] = origin
        headers["vary"] = "origin"
    return {"statusCode": status, "headers": headers, "body": json.dumps(body, default=str)}


def body_of(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode()
    try:
        b = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return b if isinstance(b, dict) else {}


def header(event: dict, name: str) -> str | None:
    h = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return h.get(name.lower())


def as_uuid(v) -> uuid.UUID:
    return uuid.UUID(str(v))
