"""Which tenant does an inbound message belong to? Answered *before* any tenant context
exists, so it cannot live in the RLS-protected database. It lives in SSM Parameter Store:

  /frontdesk/<env>/routing/widget/<sha256(widget key)[:32]>   {"tenant_id": ..., "origins": [...]}
  /frontdesk/<env>/routing/whatsapp/<phone-number-id>         {"tenant_id": ...}

Widget keys are public identifiers (they sit in a website's HTML), so only their hash is used
as the parameter name; the origin allow-list stops another site from reusing a key.
Locally, FRONTDESK_ROUTING_JSON holds the same mapping.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass

CACHE_TTL_S = 300


@dataclass(frozen=True)
class Route:
    tenant_id: uuid.UUID
    origins: tuple[str, ...] = ()


def key_hash(widget_key: str) -> str:
    return hashlib.sha256(widget_key.encode()).hexdigest()[:32]


def new_widget_key() -> str:
    return "fdk_live_" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789") for _ in range(32))


def _prefix() -> str:
    return os.environ.get("FRONTDESK_SSM_PREFIX", "/frontdesk/local")


@functools.cache
def _local() -> dict:
    return json.loads(os.environ.get("FRONTDESK_ROUTING_JSON", "{}"))


_cache: dict[str, tuple[float, dict | None]] = {}


def _get(name: str) -> dict | None:
    if "FRONTDESK_ROUTING_JSON" in os.environ:
        return _local().get(name)
    hit = _cache.get(name)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    import boto3

    try:
        v = json.loads(boto3.client("ssm").get_parameter(Name=f"{_prefix()}/{name}")["Parameter"]["Value"])
    except Exception:
        v = None
    _cache[name] = (time.time(), v)
    return v


def for_widget(widget_key: str | None, origin: str | None) -> Route | None:
    if not widget_key:
        return None
    v = _get(f"routing/widget/{key_hash(widget_key)}")
    if not v:
        return None
    origins = tuple(v.get("origins", []))
    if origins and origin not in origins:
        return None
    return Route(uuid.UUID(v["tenant_id"]), origins)


def for_whatsapp(phone_number_id: str) -> Route | None:
    v = _get(f"routing/whatsapp/{phone_number_id}")
    return Route(uuid.UUID(v["tenant_id"])) if v else None


def tenant_ids() -> list[uuid.UUID]:
    v = _get("tenants") or {"tenant_ids": []}
    return [uuid.UUID(t) for t in v.get("tenant_ids", [])]
