"""Drills: break something on purpose, in the demo tenant, and record what the business saw.

    python -m frontdesk.drills model-down --dsn postgresql://...      (local, writes nothing outside the demo tenant)
    python -m frontdesk.drills model-down --target aws --profile frontdesk-dev --record docs/drills

A drill is evidence only if it can be run again and fails loudly when the behaviour regresses,
so each step states what must happen, what did happen, and passes or fails; the run exits
non-zero on any failure and backend/tests/test_drills.py runs it on every pull request. Drills
touch the demo tenant only, so they can run in dev at any time without a change window.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from . import inbox_api, insights, pipeline, services, telemetry
from .db import Database
from .llm.base import ModelUnavailable
from .tenancy import DEMO_TENANT_ID


@dataclass
class Step:
    name: str
    expected: str
    observed: str = ""
    passed: bool = False
    ms: float = 0.0


class ThrottledBedrock:
    """What Bedrock looks like when it throttles or the Region has an incident."""

    name = "bedrock-throttled"

    def draft(self, req, *, large=False, run_tool=None):
        raise ModelUnavailable("ThrottlingException: Too many requests, please wait before trying again.")

    def summarise(self, lines):
        raise ModelUnavailable("ThrottlingException")


class CrashingModel:
    """A failure the agent does not anticipate: the Step Functions Catch is the only net."""

    name = "crash"

    def draft(self, req, *, large=False, run_tool=None):
        raise RuntimeError("Task timed out after 30.00 seconds")

    def summarise(self, lines):
        raise RuntimeError("Task timed out")


def _inbound(text: str, name: str = "Drill customer", msg_id: str | None = None, address: str | None = None) -> pipeline.Inbound:
    return pipeline.Inbound(DEMO_TENANT_ID, "whatsapp", address or f"+2799{uuid.uuid4().int % 10**7:07d}", text, name, msg_id)


def _owner_claims(db: Database) -> dict:
    with db.transaction(DEMO_TENANT_ID) as tx:
        email = tx.scalar("SELECT email FROM app.staff WHERE active AND role = 'owner' ORDER BY display_name LIMIT 1")
    return {"sub": f"drill-{email}", "email": email, "custom:tenant_id": str(DEMO_TENANT_ID), "cognito:groups": ["owner"]}


def model_down(db: Database) -> list[Step]:
    steps: list[Step] = []

    def step(name: str, expected: str, fn: Callable[[], tuple[bool, str]]) -> None:
        s, t0 = Step(name, expected), time.perf_counter()
        try:
            s.passed, s.observed = fn()
        except Exception as e:  # noqa: BLE001 -- a drill reports every failure instead of stopping
            s.passed, s.observed = False, f"raised {type(e).__name__}: {str(e)[:160]}"
        s.ms = round((time.perf_counter() - t0) * 1000, 1)
        steps.append(s)

    down = services.build("offline")
    down.model = ThrottledBedrock()
    telemetry.CAPTURED.clear()
    convs: dict[str, str] = {}

    def price_question():
        rec = pipeline.receive(db, _inbound("How much is the Home 5kW backup package?"))
        o = pipeline.answer(db, down, DEMO_TENANT_ID, rec)
        convs["price"] = rec.conversation_id
        ok = o.action == "degraded" and "will reply" in o.reply and o.assigned_staff is not None
        return ok, f"action={o.action}; staff={(o.assigned_staff or {}).get('display_name')}; reply starts '{o.reply.split(chr(10))[-1][:70]}'"

    step("A price question while Bedrock throttles", "customer told a person will reply; conversation assigned to a named person", price_question)

    def person_request():
        rec = pipeline.receive(db, _inbound("I want to talk to a person"))
        o = pipeline.answer(db, down, DEMO_TENANT_ID, rec)
        convs["person"] = rec.conversation_id
        return o.action == "handoff" and o.handoff_reason == "human_requested", f"action={o.action}; reason={o.handoff_reason}"

    step("A request for a person while Bedrock throttles", "the rules hand over as normal: no model needed", person_request)

    def stop():
        address = f"+2799{uuid.uuid4().int % 10**7:07d}"
        rec = pipeline.receive(db, _inbound("Hi, what are your hours?", address=address))
        pipeline.answer(db, down, DEMO_TENANT_ID, rec)
        rec2 = pipeline.receive(db, _inbound("STOP", address=address))
        o = pipeline.answer(db, down, DEMO_TENANT_ID, rec2)
        return o.action == "opted_out", f"action={o.action}"

    step("STOP while Bedrock throttles", "the opt-out is honoured without a model", stop)

    def retry():
        mid = f"wamid.drill.{uuid.uuid4().hex[:12]}"
        address = f"+2799{uuid.uuid4().int % 10**7:07d}"
        first = pipeline.receive(db, _inbound("Do you install in Tzaneen?", msg_id=mid, address=address))
        pipeline.answer(db, down, DEMO_TENANT_ID, first)
        again = pipeline.receive(db, _inbound("Do you install in Tzaneen?", msg_id=mid, address=address))
        return first.proceed and not again.proceed, f"first proceed={first.proceed}; retry proceed={again.proceed}"

    step("WhatsApp redelivers the same message during the outage", "answered once, not twice", retry)

    def crash():
        crashing = services.build("offline")
        crashing.model = CrashingModel()
        rec = pipeline.receive(db, _inbound("What does the warranty cover?"))
        try:
            pipeline.answer(db, crashing, DEMO_TENANT_ID, rec)
            return False, "the crash did not surface, so the Catch would not run"
        except RuntimeError as e:
            # This is the Step Functions Catch -> Degrade branch (infra/lib/workload/app-stack.ts).
            o = pipeline.degrade(db, DEMO_TENANT_ID, rec, str(e))
            convs["crash"] = rec.conversation_id
            return o.action == "degraded" and o.assigned_staff is not None, f"Catch ran degrade: action={o.action}; staff={(o.assigned_staff or {}).get('display_name')}"

    step("The answer Lambda times out outright", "the workflow's Catch still replies and hands over", crash)

    def queue():
        claims = _owner_claims(db)
        r = inbox_api.handle(db, "GET", "/v1/inbox/handoffs", claims)
        import json

        body = json.loads(r["body"])
        mine = [h for h in body if h["conversation_id"] in convs.values()]
        with_summary = [h for h in mine if h.get("summary")]
        return r["statusCode"] == 200 and len(mine) == len(convs) and len(with_summary) == len(mine), \
            f"{len(mine)} of {len(convs)} drill conversations in the queue, {len(with_summary)} with a summary"

    step("Staff open the inbox", "every affected conversation is in the handoff queue with a summary", queue)

    def staff_reply():
        sent = []
        claims = _owner_claims(db)
        r = inbox_api.handle(db, "POST", f"/v1/inbox/conversations/{convs['price']}/reply", claims,
                             {"text": "Hi, the Home 5kW backup is R62 900 installed. Naledi"},
                             sender=lambda tx, ch, addr, text: sent.append(ch) or "drill-sent")
        r2 = inbox_api.handle(db, "POST", f"/v1/inbox/conversations/{convs['price']}/resolve", claims)
        return r["statusCode"] == 200 and sent == ["whatsapp"] and r2["statusCode"] == 200, f"reply {r['statusCode']}, sent via {sent}, resolve {r2['statusCode']}"

    step("A person answers and resolves", "the reply goes out on WhatsApp and the conversation closes", staff_reply)

    def dashboard():
        with db.transaction(DEMO_TENANT_ID) as tx:
            s = insights.summary(tx, dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
        reasons = dict(s["handoff_reasons"])
        return s["handed_to_staff"] >= len(convs) and reasons.get("model_unavailable", 0) >= 2, \
            f"owner summary computed from events: {s['handed_to_staff']} handed to staff, {reasons.get('model_unavailable', 0)} because the model was down"

    step("The owner checks the dashboard", "insights still compute (they read events, not the model)", dashboard)

    def detection():
        n = sum(1 for e in telemetry.CAPTURED if "Degraded" in e)
        return n >= 2, f"{n} Degraded metric points emitted (DET-06 alarms on this metric)"

    step("Operations is told", "the Degraded metric fires so DET-06 pages the operator", detection)

    def recovery():
        rec = pipeline.receive(db, _inbound("How much is the Home 5kW backup package?"))
        o = pipeline.answer(db, services.build("offline"), DEMO_TENANT_ID, rec)
        return o.action == "answer" and bool(o.citations), f"action={o.action}; citations={len(o.citations)}"

    step("Bedrock recovers", "the next message is answered with a citation; nothing to reset by hand", recovery)
    return steps


def record(steps: list[Step], target: str) -> str:
    when = dt.datetime.now(dt.UTC)
    passed = sum(s.passed for s in steps)
    lines = [f"# Drill: model down, front desk open ({when:%d %B %Y})", "",
             f"Run: `python -m frontdesk.drills model-down` against **{target}**, demo tenant only, at {when:%H:%M} UTC.",
             f"Result: **{passed} of {len(steps)} steps passed**.", "",
             "| # | Step | Must happen | What happened | Pass | ms |", "|---|---|---|---|---|---|"]
    for i, s in enumerate(steps, 1):
        lines.append(f"| {i} | {s.name} | {s.expected} | {s.observed.replace('|', '/')} | {'yes' if s.passed else '**NO**'} | {s.ms} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from .demo_tenant import connect_target

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drill", choices=["model-down"])
    ap.add_argument("--target", choices=["local", "aws"], default="local")
    ap.add_argument("--dsn")
    ap.add_argument("--profile")
    ap.add_argument("--env", default="dev")
    ap.add_argument("--record", type=pathlib.Path, help="directory to write the dated record into")
    a = ap.parse_args(argv)
    if a.target == "aws" and a.env != "dev":
        print("Drills run in dev only.", file=sys.stderr)
        return 2
    steps = model_down(connect_target(a))
    text = record(steps, "local Postgres" if a.target == "local" else f"AWS {a.env}")
    print(text)
    if a.record:
        a.record.mkdir(parents=True, exist_ok=True)
        (a.record / f"{dt.date.today():%Y-%m-%d}-model-down.run.md").write_text(text)
    return 0 if all(s.passed for s in steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
