"""The agent as an explicit, typed state machine.

Why a state machine and not "let the model decide": every path a customer message can take
is listed in TRANSITIONS, each step is a small function with one job, and the runner refuses
any transition not in the table. The trace of states is stored with every answer, so "why did
it say that?" has a precise answer, and tests can assert a path ("an injection never reaches
DRAFT").

    RECEIVED -> SCREEN -> (OPTED_OUT | OPTED_IN | REFUSE | SMALL_TALK -> SEND | POLICY)
    POLICY -> (HANDOFF | REFUSE | TOOLS)
    TOOLS -> (SEND | HANDOFF | RETRIEVE)
    RETRIEVE -> (ROUTE | HANDOFF)
    ROUTE -> (DRAFT | HANDOFF)            cost cap reached -> HANDOFF
    DRAFT -> (VALIDATE | DEGRADED)        any model failure -> DEGRADED
    VALIDATE -> (SEND | ROUTE | HANDOFF)  one retry on the large model, then HANDOFF
"""

from __future__ import annotations

import datetime as dt
import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..db import Transaction
from ..integrations.orders import ORDER_REF, OrdersAdapter
from ..llm import router
from ..llm.base import DraftRequest, LanguageModel, ModelUnavailable, Usage
from ..llm.guardrails import Guardrail, NoGuardrail
from ..llm.prompts import CURRENT as PROMPT_VERSION
from ..llm.prompts import canary, load
from ..llm.schemas import AnswerDraft, Citation
from ..llm.validate import validate
from ..redact import Redacted, redact
from ..retrieval.embedders import Embedder
from ..retrieval.rerankers import Reranker
from ..retrieval.search import Hit, hybrid_search
from . import policy, tools


class State(enum.Enum):
    RECEIVED = "received"
    SCREEN = "screen"
    SMALL_TALK = "small_talk"
    POLICY = "policy"
    TOOLS = "tools"
    RETRIEVE = "retrieve"
    ROUTE = "route"
    DRAFT = "draft"
    VALIDATE = "validate"
    SEND = "send"
    HANDOFF = "handoff"
    REFUSE = "refuse"
    OPTED_OUT = "opted_out"
    OPTED_IN = "opted_in"
    DEGRADED = "degraded"


S = State
TRANSITIONS: dict[State, set[State]] = {
    S.RECEIVED: {S.SCREEN},
    S.SCREEN: {S.OPTED_OUT, S.OPTED_IN, S.REFUSE, S.SMALL_TALK, S.POLICY, S.DEGRADED},
    S.SMALL_TALK: {S.SEND},
    S.POLICY: {S.HANDOFF, S.REFUSE, S.TOOLS},
    S.TOOLS: {S.SEND, S.HANDOFF, S.RETRIEVE},
    S.RETRIEVE: {S.ROUTE, S.HANDOFF},
    S.ROUTE: {S.DRAFT, S.HANDOFF},
    S.DRAFT: {S.VALIDATE, S.DEGRADED},
    S.VALIDATE: {S.SEND, S.ROUTE, S.HANDOFF},
}
TERMINAL = {S.SEND, S.HANDOFF, S.REFUSE, S.OPTED_OUT, S.OPTED_IN, S.DEGRADED}


class IllegalTransition(RuntimeError):
    pass


@dataclass
class Turn:
    text: str
    channel: str = "web"
    channel_address: str | None = None
    customer_name: str | None = None  # exactly as given; never used to infer a title or pronoun
    first_message: bool = False
    received_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    history: list[dict] = field(default_factory=list)


@dataclass
class Deps:
    model: LanguageModel
    embedder: Embedder
    reranker: Reranker | None
    guardrail: Guardrail = field(default_factory=NoGuardrail)
    orders: OrdersAdapter | None = None
    orders_factory: Any = None  # callable(tx) -> OrdersAdapter | None, per tenant
    over_cap: Any = None  # callable(tx) -> bool; None means no cap check (replay)
    k: int = 5


@dataclass
class Outcome:
    action: str                      # answer | handoff | refused | opted_out | degraded
    reply: str
    intent: str
    redacted: Redacted
    citations: list[Citation] = field(default_factory=list)
    handoff_reason: str | None = None
    assigned_staff: dict | None = None
    summary: str = ""
    route: str = "rules"
    route_reason: str = ""
    model_id: str = ""
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    trace: list[str] = field(default_factory=list)
    retrieved: list[Hit] = field(default_factory=list)
    model_inputs: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    validation_problems: list[str] = field(default_factory=list)
    guardrail_intervened: bool = False
    prompt_version: str = ""
    requests: list[dict] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.action == "answer"


@dataclass
class _Ctx:
    tx: Transaction
    turn: Turn
    deps: Deps
    tenant: dict
    red: Redacted | None = None
    rule: str | None = None
    intent: str = "general"
    hits: list[Hit] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    draft: AnswerDraft | None = None
    attempt: int = 0
    route: router.Route | None = None
    reason: str | None = None
    out: dict = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    model_inputs: list[str] = field(default_factory=list)
    model_id: str = ""
    problems: list[str] = field(default_factory=list)
    guardrail_hit: bool = False
    tool_ctx: tools.ToolContext | None = None


class Agent:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # ----- runner -----
    def run(self, tx: Transaction, turn: Turn) -> Outcome:
        t0 = time.perf_counter()
        tenant = tx.one("SELECT name, assistant_name, timezone FROM app.tenants") or {"name": "", "assistant_name": "assistant", "timezone": "Africa/Johannesburg"}
        ctx = _Ctx(tx, turn, self.deps, tenant)
        orders = self.deps.orders if self.deps.orders is not None else (self.deps.orders_factory(tx) if self.deps.orders_factory else None)
        ctx.tool_ctx = tools.ToolContext(tx, turn.channel_address, orders)
        state, trace = S.RECEIVED, [S.RECEIVED.value]
        while state not in TERMINAL:
            try:
                nxt = getattr(self, f"_{state.value}")(ctx)
            except ModelUnavailable as e:
                ctx.reason, ctx.problems = "model_unavailable", ctx.problems + [str(e)[:200]]
                nxt = S.DEGRADED
            if nxt not in TRANSITIONS[state]:
                raise IllegalTransition(f"{state.value} -> {nxt.value}")
            state = nxt
            trace.append(state.value)
        return self._finish(ctx, state, trace, (time.perf_counter() - t0) * 1000)

    # ----- states -----
    def _received(self, ctx: _Ctx) -> State:
        return S.SCREEN

    def _screen(self, ctx: _Ctx) -> State:
        ctx.red = redact(ctx.turn.text)
        text = ctx.red.text
        if policy.is_opt_out(text):
            return S.OPTED_OUT
        if policy.is_marketing_opt_in(text):
            return S.OPTED_IN
        decision = policy.evaluate(text)
        ctx.rule = decision.rule
        ctx.intent = policy.keyword_intent(text)
        if decision.rule == "injection":
            ctx.reason = "injection"
            return S.REFUSE
        g = ctx.deps.guardrail.check_input(text)
        if g.intervened:
            ctx.guardrail_hit, ctx.reason = True, "injection"
            return S.REFUSE
        if policy.is_small_talk(text):
            return S.SMALL_TALK
        return S.POLICY

    def _small_talk(self, ctx: _Ctx) -> State:
        low = ctx.red.text.lower()
        thanks = any(w in low for w in ("thank", "leboga", "dankie"))
        ctx.out["reply"] = ("You're welcome. Is there anything else I can help with?" if thanks
                            else f"Hello! How can I help you with {ctx.tenant['name']} today?")
        ctx.out["small_talk"] = True
        return S.SEND

    def _policy(self, ctx: _Ctx) -> State:
        if ctx.rule:
            ctx.reason = ctx.rule
            return S.HANDOFF
        return S.TOOLS

    def _tools(self, ctx: _Ctx) -> State:
        text = ctx.red.text
        if m := ORDER_REF.search(text):
            r = tools.call(ctx.tool_ctx, "get_order_status", {"order_ref": m.group(1)})
            ctx.tool_results.append(r)
            ctx.intent = "order_status"
            if r.get("ok"):
                ctx.out["reply"] = r["result"]
                return S.SEND
            ctx.reason = r.get("needs", "unanswerable")
            return S.HANDOFF
        if _wants_site_visit(text):
            place = _place_in(text, ctx) or "not given"
            r = tools.call(ctx.tool_ctx, "request_site_assessment", {"place": place, "preferred_time": ""})
            ctx.tool_results.append(r)
            ctx.reason = "booking_commitment"
            return S.HANDOFF
        return S.RETRIEVE

    def _retrieve(self, ctx: _Ctx) -> State:
        ctx.hits = hybrid_search(ctx.tx, ctx.red.text, ctx.deps.embedder, k=ctx.deps.k, reranker=ctx.deps.reranker)
        if not ctx.hits:
            ctx.reason = "unanswerable"
            return S.HANDOFF
        return S.ROUTE

    def _route(self, ctx: _Ctx) -> State:
        if ctx.deps.over_cap is not None and ctx.deps.over_cap(ctx.tx):
            ctx.reason = "cost_cap"
            return S.HANDOFF
        if ctx.attempt > 1:
            ctx.reason = "validation_failed"
            return S.HANDOFF
        ctx.route = router.choose(ctx.red.text, ctx.hits, attempt=ctx.attempt)
        return S.DRAFT

    def _draft(self, ctx: _Ctx) -> State:
        req = DraftRequest(
            question=ctx.red.text, sources=ctx.hits, tool_results=ctx.tool_results, history=ctx.turn.history,
            customer_name=ctx.turn.customer_name, tenant_name=ctx.tenant["name"], assistant_name=ctx.tenant["assistant_name"],
            known_entities=_known_places(ctx),
        )
        res = ctx.deps.model.draft(req, large=ctx.route.tier == "large",
                                   run_tool=lambda n, a: _record_tool(ctx, tools.call(ctx.tool_ctx, n, a)))
        ctx.draft, ctx.model_id = res.draft, res.model_id
        ctx.usage.add(res.usage)
        ctx.model_inputs += res.model_inputs
        return S.VALIDATE

    def _validate(self, ctx: _Ctx) -> State:
        d = ctx.draft
        v = validate(d, ctx.hits, tool_results=ctx.tool_results, canary=canary())
        if "canary_leak" in v.problems:
            ctx.problems += v.problems
            ctx.reason = "injection"
            ctx.out["canary_leak"] = True
            return S.HANDOFF
        if d.action == "handoff":
            ctx.reason = d.handoff_reason or "unanswerable"
            return S.HANDOFF
        if v.ok:
            g = ctx.deps.guardrail.check_output(d.answer, "\n".join(h.text for h in ctx.hits), ctx.red.text)
            if g.intervened:
                ctx.guardrail_hit = True
                v.problems.append("guardrail_output:" + ",".join(g.reasons))
            else:
                ctx.out["reply"] = d.answer
                return S.SEND
        ctx.problems += v.problems
        ctx.attempt += 1
        return S.ROUTE

    # ----- finishing -----
    def _finish(self, ctx: _Ctx, state: State, trace: list[str], ms: float) -> Outcome:
        red = ctx.red or redact(ctx.turn.text)
        o = Outcome(action="", reply="", intent=ctx.intent, redacted=red, trace=trace, latency_ms=ms,
                    usage=ctx.usage, model_inputs=ctx.model_inputs, retrieved=ctx.hits, tool_calls=ctx.tool_results,
                    validation_problems=ctx.problems, guardrail_intervened=ctx.guardrail_hit, model_id=ctx.model_id,
                    route=ctx.route.tier if ctx.route else "rules", route_reason=ctx.route.reason if ctx.route else (ctx.rule or ""),
                    prompt_version=f"{PROMPT_VERSION}@{load().sha}" if ctx.route else "", requests=ctx.tool_ctx.requests)
        when = _when(ctx)
        if state == S.SEND:
            o.action, o.reply = "answer", ctx.out["reply"]
            o.citations = ctx.draft.citations if ctx.draft and not ctx.out.get("small_talk") else []
        elif state == S.HANDOFF:
            o.action, o.handoff_reason = "handoff", ctx.reason or "unanswerable"
            o.assigned_staff = pick_staff(ctx.tx, o.handoff_reason, ctx.intent)
            o.reply = handoff_reply(o.handoff_reason, o.assigned_staff, when, ctx.tenant["name"])
            o.summary = summary_lines(ctx, o)
        elif state == S.REFUSE:
            o.action, o.handoff_reason = "refused", ctx.reason or "injection"
            o.reply = (f"I can't do that. I only answer questions about {ctx.tenant['name']} from our published information. "
                       "Reply PERSON if you would like to talk to a person.")
        elif state == S.OPTED_IN:
            o.action, o.intent = "opted_in", "marketing_opt_in"
            o.reply = (f"Thanks: you'll get occasional updates and offers from {ctx.tenant['name']}. "
                       "Send STOP at any time and they stop.")
        elif state == S.OPTED_OUT:
            o.action, o.intent = "opted_out", "opt_out"
            o.reply = "Done: you won't get any more messages from us. Send us a message any time if you need us again."
        else:  # DEGRADED
            o.action, o.handoff_reason = "degraded", ctx.reason or "model_unavailable"
            o.assigned_staff = pick_staff(ctx.tx, o.handoff_reason, ctx.intent)
            o.reply = f"Thanks for your message. A person from {ctx.tenant['name']} will reply to you here {when}."
            o.summary = summary_lines(ctx, o)
        if ctx.turn.first_message and o.action not in ("opted_out", "opted_in"):
            o.reply = disclosure(ctx.tenant) + "\n\n" + o.reply
        return o


# ----- helpers (module level so they are testable on their own) -----

def disclosure(tenant: dict) -> str:
    # Refusal 1: the first message says it is an assistant, and how to reach a person.
    return (f"Hi, I'm {tenant['assistant_name']}, an automated assistant for {tenant['name']}. "
            "I answer from the company's published information. Reply PERSON at any time to talk to a person.")


TOPIC_FOR = {
    "negotiation": "pricing", "unanswerable": None, "booking_commitment": "booking", "refund": "payment",
    "fault": "fault", "complaint": None, "verification_needed": "order_status", "human_requested": None,
}


def pick_staff(tx: Transaction, reason: str, intent: str) -> dict | None:
    """A handoff always names a person: the staff member whose topics match, else the owner."""
    staff = tx.execute("SELECT staff_id::text AS staff_id, display_name, role, handles_topics FROM app.staff WHERE active ORDER BY role DESC, display_name")
    topic = TOPIC_FOR.get(reason) or intent
    for s in staff:
        topics = s["handles_topics"] if isinstance(s["handles_topics"], list) else []
        if topic in topics:
            return s
    owners = [s for s in staff if s["role"] == "owner"]
    return owners[0] if owners else (staff[0] if staff else None)


def handoff_reply(reason: str, staff: dict | None, when: str, tenant_name: str) -> str:
    who = staff["display_name"] if staff else f"someone at {tenant_name}"
    lines = {
        "human_requested": f"Of course. I've asked {who} to reply to you here {when}.",
        "negotiation": f"I can't change prices or offer discounts. I've passed your request to {who}, who will reply here {when}.",
        "unanswerable": f"I don't have that in our published information, so I've asked {who} to reply to you here {when}.",
        "booking_commitment": f"I can't book or confirm dates myself. I've asked {who} to contact you {when} to arrange it.",
        "refund": f"I can't approve refunds. I've passed your request to {who}, who will reply here {when}.",
        "complaint": f"I'm sorry to hear that. I've passed your message to {who}, who will reply to you here {when}.",
        "fault": f"I've flagged this as a fault for {who}, who will reply to you here {when}.",
        "out_of_scope": f"I can only help with questions about {tenant_name}. I've asked {who} to reply in case they can help.",
        "verification_needed": f"I can't share order details on this conversation. I've asked {who} to check and reply {when}.",
        "validation_failed": f"I want to be sure I give you the right answer, so I've asked {who} to reply to you here {when}.",
        "cost_cap": f"A person from {tenant_name} will reply to you here {when}.",
        "model_unavailable": f"A person from {tenant_name} will reply to you here {when}.",
        "injection": f"I've asked {who} to reply to you here {when}.",
    }
    return lines.get(reason, lines["unanswerable"])


REASON_TEXT = {
    "human_requested": "asked for a person", "negotiation": "asked for a discount or price change",
    "unanswerable": "the documents do not answer this", "booking_commitment": "wants a date booked or a visit arranged",
    "refund": "asked for a refund", "complaint": "is unhappy (complaint)", "fault": "reported a system fault",
    "out_of_scope": "asked something outside the business", "verification_needed": "asked about an order the number cannot be matched to",
    "validation_failed": "the assistant's draft failed checks", "cost_cap": "monthly assistant budget reached",
    "model_unavailable": "the assistant was unavailable (degraded mode)", "injection": "message looked like a manipulation attempt",
}


def summary_lines(ctx: _Ctx, o: Outcome) -> str:
    """Three lines for staff, from redacted text only. The Bedrock pipeline may replace this
    with a model-written summary; this template is the fallback that always works."""
    name = ctx.turn.customer_name or "The customer"
    q = (ctx.red.text if ctx.red else "")[:160]
    did = "nothing yet" if not ctx.hits else f"found {len(ctx.hits)} related document sections"
    if ctx.tool_results:
        did = "; ".join(str(t.get("result", ""))[:80] for t in ctx.tool_results)
    return f"{name} wrote: \"{q}\"\nAssistant: {did}; did not answer.\nNeeds: {REASON_TEXT.get(o.handoff_reason or '', o.handoff_reason)}."


def _record_tool(ctx: _Ctx, result: dict) -> dict:
    ctx.tool_results.append(result)
    return result


def _known_places(ctx: _Ctx) -> list[str]:
    row = ctx.tx.one("SELECT value FROM app.tenant_config WHERE key = 'service_zones'")
    if not row:
        return []
    v = row["value"] if isinstance(row["value"], dict) else __import__("json").loads(row["value"])
    return [p for z in v.values() for p in z.get("places", [])]


def _place_in(text: str, ctx: _Ctx) -> str | None:
    low = text.lower()
    return next((p for p in _known_places(ctx) if p.lower() in low), None)


def _wants_site_visit(text: str) -> bool:
    import re

    return bool(re.search(r"\b(i'?d like|i want|please|can you|could you|arrange|request)\b.{0,30}\b(site assessment|site visit|"
                          r"come (and )?(look|see) (at )?(my|the) (house|roof|home|property))\b", text, re.I))


DEFAULT_HOURS = {"mon-fri": ["08:00", "17:00"], "sat": ["08:00", "12:00"], "sun": None}


def office_hours(tx: Transaction) -> dict:
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'office_hours'")
    if not row:
        return DEFAULT_HOURS
    return row["value"] if isinstance(row["value"], dict) else __import__("json").loads(row["value"])


def _when(ctx: _Ctx) -> str:
    from zoneinfo import ZoneInfo

    hours = office_hours(ctx.tx)
    local = ctx.turn.received_at.astimezone(ZoneInfo(ctx.tenant.get("timezone") or "Africa/Johannesburg"))
    if is_office_hours(local, hours):
        return "shortly"
    parts = []
    if hours.get("mon-fri"):
        parts.append(f"Monday to Friday {hours['mon-fri'][0]} to {hours['mon-fri'][1]}")
    if hours.get("sat"):
        parts.append(f"Saturday {hours['sat'][0]} to {hours['sat'][1]}")
    return f"when the office opens ({', '.join(parts)})" if parts else "as soon as possible"


def is_office_hours(local: dt.datetime, hours: dict | None = None) -> bool:
    hours = hours or DEFAULT_HOURS
    key = "mon-fri" if local.weekday() < 5 else "sat" if local.weekday() == 5 else "sun"
    span = hours.get(key)
    if not span:
        return False
    to_h = lambda s: int(s[:2]) + int(s[3:5]) / 60
    h = local.hour + local.minute / 60
    return to_h(span[0]) <= h < to_h(span[1])


def new_id() -> str:
    return str(uuid.uuid4())
