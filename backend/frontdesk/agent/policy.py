"""Rules that decide, before any model call, whether a message must go to a person.

Transparent and cheap on purpose. Each rule exists because of a refusal in AGENTS.md: a person
is always reachable, negotiation and refunds are never automated, complaints and faults get a
human. The trained classifier (ml/frontdesk_ml/classifier.py) is compared against these rules
on the same labelled set; the rules stay as a floor because they are auditable line by line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_F = re.I

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("injection", re.compile(
        r"\b(ignore|disregard|forget) (all |any |your |the |previous |prior )*(instructions|rules|prompt)"
        r"|\bsystem prompt\b|\b(admin|developer|god|dan) mode\b|\byou are now\b|\bjailbreak\b"
        r"|\b(print|reveal|show|repeat) (me )?(your|the) (system |hidden )?(prompt|instructions|rules)\b"
        r"|^\s*system\s*:|\b(just|only) (reply|say|answer|type)( with)? [\"']?(yes|no|approved|ok)\b"
        r"|\b(list|give|show|send|tell)( me)? (the )?(names|numbers|phone numbers|addresses|details|contacts) of\b"
        r"|\b(phone number|number|address|name|details) of (the|your|another|other|a) (customer|client|person)", _F)),
    ("human_requested", re.compile(
        r"\b(talk|speak|chat)\s+(to|with)\s+(a |an |some)?(real )?(person|human|someone|somebody|agent|consultant|manager)\b"
        r"|\breal person\b|\bhuman\b|\bnot a bot\b|\bare you a bot\b|\blet me (speak|talk) to\b"
        r"|\b(can|could) (someone|somebody|a person|a real person|you) (call|phone) me\b|\bcall me\b"
        r"|^\s*(person|agent|human)\s*[.!]*\s*$|\bbolela le motho\b|\bmotho\b", _F)),
    ("fault", re.compile(r"^\s*fault\b|\bbeeping\b|\berror code\b|\bF\d{2}\b|\bnot working\b|\btripping\b|\bsparks?\b|\bsmoke\b", _F)),
    ("complaint", re.compile(
        r"\bworst\b|\bunhappy\b|\bcomplain|\breport (one of )?your\b|\brude\b|\bnever (pitched|arrived|came)\b"
        r"|\bnobody (answers|replies|helps)\b|\bterrible\b|\buseless\b|\bangry\b|\bdisappointed\b|\bscam\b", _F)),
    ("refund", re.compile(r"\b(want|need|demand|give)\b.{0,25}\b(refund(ed)?|money back|deposit back)\b", _F)),
    ("negotiation", re.compile(
        r"\b(can|could|will|would) you (do|give|make|match|drop|reduce|lower|beat)\b"
        r"|\bR\s?\d[\d\s]*\s+(less|cheaper|off)\b|\b\d+\s?(%|percent) off\b|\bfor free\b|\bcheaper quote\b"
        r"|\bmatch (it|the price|their|that)\b|\bbest price\b|\bnegotiat|\bgive me (a |the )?.{0,30}(discount|price|deal|free)\b"
        r"|\b(nthusa|help me|can i get|give me).{0,15}\bdiscount\b|\bwill you give me a discount\b|\bsaid i (get|can get)\b"
        r"|\b(get|have|sell me|give me) (the |a )?.{0,25}\b(at|for) (the |a )?.{0,15}\bprice\b", _F)),
    ("booking_commitment", re.compile(
        r"^\s*(please\s+)?(book|confirm|schedule|reserve)\b|\b(can|could) you (book|confirm|schedule|reserve)\b"
        r"|\bconfirm my (installation|booking|date)\b|\bi'?d like to (book|schedule) (an? |my )?(installation|booking)\b"
        r"|\bbook me\b", _F)),
    ("out_of_scope", re.compile(
        r"\bshould i (take|get|apply for) a loan\b|\binvest(ment|ing)?\b(?!.*\bsolar\b)|\blawyer\b|\bsue\b"
        r"|\bwrite me (an? )?(essay|poem|story|code)\b|\bhomework\b|\bclimate change\b|\bpolitic", _F)),
]

SMALL_TALK = re.compile(
    r"^\s*(hi|hey|hello|howzit|dumela(ng)?|good (morning|afternoon|evening)|thanks?( you)?( so much)?|thank you|ke a leboga|"
    r"sharp|ok(ay)?|cool|great|baie dankie|dankie|goeie (more|middag))[\s!.,]*$", _F)

OPT_OUT = re.compile(r"^\s*(stop|unsubscribe|opt[- ]?out|emisa|ema|stop messages)\s*[.!]*\s*$", _F)


@dataclass(frozen=True)
class PolicyDecision:
    rule: str | None

    @property
    def needs_person(self) -> bool:
        return self.rule is not None


def evaluate(text: str) -> PolicyDecision:
    for name, rx in RULES:
        if rx.search(text):
            return PolicyDecision(name)
    return PolicyDecision(None)


def is_small_talk(text: str) -> bool:
    return bool(SMALL_TALK.match(text))


# Marketing opt-in must be explicit and unambiguous (POPIA s69): an exact keyword, never inferred
# from "yes" or "thanks", and never from being answered.
OPT_IN_MARKETING = re.compile(r"^\s*(join updates|subscribe me|yes to updates)\s*[.!]*\s*$", _F)


def is_marketing_opt_in(text: str) -> bool:
    return bool(OPT_IN_MARKETING.match(text))


def is_opt_out(text: str) -> bool:
    return bool(OPT_OUT.match(text))


INTENT_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("order_status", re.compile(r"\border\b|\bBSE-\d+", _F)),
    ("warranty", re.compile(r"warrant|waranty|waarborg|tiisetso|guarantee|claim", _F)),
    ("cancellation", re.compile(r"cancel", _F)),
    ("booking", re.compile(r"\bbook|schedul|date\b|site assessment|move my", _F)),
    ("payment", re.compile(r"\bpay|card|cash|eft|finance|loan|deposit|balance|lefa", _F)),
    ("hours", re.compile(r"\bopen\b|hours|saturday|sunday|mokibelo|saterdag|bula|oop\b", _F)),
    ("location", re.compile(r"where (is|are)|showroom|address|\ble kae\b", _F)),
    ("service_area", re.compile(r"travel|do you (install|work) in|\bin (mankweng|tzaneen|bendor|marble hall|musina|mokopane|lebowakgomo)", _F)),
    ("discount", re.compile(r"discount|cheaper|less\b|% off|percent off|free\b|match", _F)),
    ("installation", re.compile(r"install|roof|geyser|municipal|coc\b|certificate|battery last|app\b|service|clean|tsenya", _F)),
    ("pricing", re.compile(r"price|cost|how much|charge|theko|bokae|hoeveel|kos\b|\bR\d", _F)),
]


def keyword_intent(text: str) -> str:
    """Rules baseline for intent; compared with the trained classifier in Phase 4."""
    d = evaluate(text)
    if d.rule in ("human_requested",):
        return "human"
    if d.rule in ("complaint",):
        return "complaint"
    if d.rule == "fault":
        return "fault"
    if d.rule == "out_of_scope":
        return "out_of_scope"
    for name, rx in INTENT_KEYWORDS:
        if rx.search(text):
            return name
    return "general"
