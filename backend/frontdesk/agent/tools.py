"""Typed, tenant-scoped tools.

The model may call these by name with arguments that match a pydantic schema. Two properties
make them safe to expose to a model that reads hostile text:

- No tool takes a tenant id. The tenant comes from the open transaction (RLS) and the
  ToolContext, which the model cannot touch. A test asserts no schema has a tenant field.
- No tool commits the business. The only "write" tool asks a person to call back; it never
  confirms a date, a price or a discount (refusal 3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..db import Transaction
from ..integrations.orders import OrdersAdapter


class _Args(BaseModel):
    # Extra keys are rejected, so a model cannot smuggle `tenant_id` or anything else in.
    model_config = ConfigDict(extra="forbid")


class CheckServiceAreaArgs(_Args):
    place: str = Field(min_length=2, max_length=60, description="Suburb or town the customer named")


class OrderStatusArgs(_Args):
    order_ref: str = Field(pattern=r"^[A-Za-z]{2,4}-\d{3,6}$", description="Order reference, e.g. BSE-1042")


class SiteAssessmentArgs(_Args):
    place: str = Field(min_length=2, max_length=60)
    preferred_time: str = Field(default="", max_length=60, description="What the customer said about timing, verbatim")


@dataclass
class ToolContext:
    tx: Transaction
    channel_address: str | None
    orders: OrdersAdapter | None
    requests: list[dict] = field(default_factory=list)  # callback requests raised during this turn


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    run: Callable[[ToolContext, Any], dict]
    hands_over: bool = False

    def spec(self) -> dict:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {"toolSpec": {"name": self.name, "description": self.description, "inputSchema": {"json": schema}}}


def _zones(tx: Transaction) -> dict:
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'service_zones'")
    if not row:
        return {}
    v = row["value"]
    return v if isinstance(v, dict) else __import__("json").loads(v)


def check_service_area(ctx: ToolContext, a: CheckServiceAreaArgs) -> dict:
    place = a.place.strip().lower()
    for zone, z in _zones(ctx.tx).items():
        if any(place == p.lower() for p in z.get("places", [])):
            return {"ok": True, "result": f"{a.place} is in Zone {zone}. Travel fee R{z['travel_fee_zar']:,}".replace(",", " ")
                    + f"; site assessment R{z['assessment_fee_zar']:,}.".replace(",", " "), "zone": zone}
    return {"ok": False, "result": f"{a.place} is not in our listed service zones."}


def get_order_status(ctx: ToolContext, a: OrderStatusArgs) -> dict:
    if ctx.orders is None:
        return {"ok": False, "result": "order lookup is not connected for this business"}
    s = ctx.orders.status(a.order_ref, requester_address=ctx.channel_address)
    if s is None:
        # Same answer whether the order does not exist or belongs to someone else: no oracle.
        return {"ok": False, "result": "I can't share order details on this conversation.", "needs": "verification_needed"}
    return {"ok": True, "result": f"Order {s.ref}: {s.detail}"}


def request_site_assessment(ctx: ToolContext, a: SiteAssessmentArgs) -> dict:
    ctx.requests.append({"kind": "site_assessment", "place": a.place, "preferred_time": a.preferred_time})
    return {"ok": True, "result": "A staff member will contact you to arrange the site assessment.", "handoff": "booking_commitment"}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("check_service_area", "Look up whether a place is in the business's service zones and its travel fee.", CheckServiceAreaArgs, check_service_area),
    Tool("get_order_status", "Look up the status of the customer's own order by reference.", OrderStatusArgs, get_order_status),
    Tool("request_site_assessment", "Ask staff to contact the customer to arrange a site assessment. Does not book a date.", SiteAssessmentArgs, request_site_assessment, hands_over=True),
]}


def call(ctx: ToolContext, name: str, raw_args: dict) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool {name}"}
    try:
        args = tool.args.model_validate(raw_args)
    except ValidationError as e:
        return {"ok": False, "error": f"invalid arguments: {e.errors()[0]['msg']}"}
    out = tool.run(ctx, args)
    out["tool"] = name
    return out


def specs() -> list[dict]:
    return [t.spec() for t in TOOLS.values()]
