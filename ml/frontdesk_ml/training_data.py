"""Synthetic training data for the handoff/intent classifier.

Built from templates and slot fillers with a fixed seed, so it is reproducible and clearly
synthetic. It is deliberately *not* drawn from the evaluation sets: the classifier is scored
on evals/datasets/*.yaml, which it has never seen, and ml/tests/test_classifier_training.py
fails the build if any evaluation question appears here (it caught twelve in the first draft). When a client's real, consented and
redacted messages exist, they replace this (refusal 6: never across tenants without consent).

    python -m frontdesk_ml.training_data   (writes evals/datasets/train.synthetic.jsonl)
"""

from __future__ import annotations

import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "evals" / "datasets" / "train.synthetic.jsonl"

PRODUCTS = ["3kW backup", "5kW backup", "5kW hybrid", "8kW system", "12kW three-phase", "battery", "extra panel", "geyser timer", "inverter", "solar system"]
PLACES = ["Polokwane", "Seshego", "Bendor", "Mankweng", "Tzaneen", "Mokopane", "Lebowakgomo", "Marble Hall", "Flora Park"]
NAMES = ["Naledi", "Pieter", "Thabo", "the manager", "someone"]
DAYS = ["Monday", "Tuesday", "next week", "the 3rd", "tomorrow", "Friday morning"]

# (intent, handoff reason or None, templates)
TEMPLATES: list[tuple[str, str | None, list[str]]] = [
    ("pricing", None, ["How much is the {p}?", "price for the {p} please", "what does the {p} cost installed", "hw much 4 the {p}",
                       "Theko ya {p} ke bokae?", "Hoeveel kos die {p}?", "Can you send me the price of the {p}", "cost of {p} in {pl}?"]),
    ("warranty", None, ["what warranty comes with the {p}", "{p} warranty?", "what does the warranty not cover", "how do I claim warranty",
                        "waranty on the {p} how many yrs", "Tiisetso ya {p}?", "is my {p} still under warranty after 2 years"]),
    ("installation", None, ["How long does it take to install the {p}?", "do I need to be home for the install", "can you install on a tin roof",
                            "will the {p} run my geyser", "do I need municipal approval", "how many hours will the battery last in loadshedding",
                            "Go tsenya {p} go tsea nako e kae?"]),
    ("payment", None, ["card machine available?", "bank transfer ok?", "how much deposit do I pay", "the rest of the money is due when", "Na le amogela card?"]),
    ("hours", None, ["What time do you open?", "are you open on Saturday", "closing time today?", "Le bula neng?", "Is julle oop op Sondag?"]),
    ("location", None, ["showroom address pls", "what is your address", "Le kae?", "where can I come see you"]),
    ("service_area", None, ["Do you install in {pl}?", "travel fee to {pl}?", "you guys do jobs in {pl}?", "Le tsenya ko {pl}?"]),
    ("booking", None, ["whats the process to get an install date", "can I change my installation date", "how do bookings work"]),
    ("order_status", None, ["any news on order BSE-1077", "where is my order", "has my order BSE-1061 been installed", "order update please"]),
    ("general", None, ["Do you speak Afrikaans?", "who handles accounts", "thanks a lot", "who do I talk to about payments", "are you a company in Polokwane"]),
    ("human", "human_requested", ["i need a human agent", "can a real person call me", "let me speak to {n}", "human please", "are you a bot? I want a human",
                                  "Ke kopa go bolela le motho", "connect me to {n} please", "I need to speak with someone now"]),
    ("discount", "negotiation", ["Can you do the {p} for R5 000 less?", "give me a discount on the {p}", "I got a cheaper quote, can you match it",
                                 "if I pay cash will you drop the price", "best price for the {p}?", "le ka nthusa ka discount?",
                                 "can I get the {p} at the backup price", "{n} said I get 20% off"]),
    ("complaint", "complaint", ["Your technician never arrived, worst service", "I am very unhappy with the installation", "nobody answers my calls",
                                "I want to complain about {n}", "this is terrible, my system is off for days", "your installer was rude"]),
    ("fault", "fault", ["FAULT inverter beeping", "my {p} shows error F23", "the system keeps tripping", "there is smoke from the inverter",
                        "inverter not working since yesterday", "the battery is beeping and not charging"]),
    ("cancellation", "refund", ["I want my deposit refunded", "give me my money back", "refund me now I changed my mind", "I need a refund for the {p}"]),
    ("booking", "booking_commitment", ["Please book me for {d}", "confirm my installation for {d}", "book the {p} install for {d}",
                                       "schedule my installation {d} please", "can you book me in for {d}"]),
    ("out_of_scope", "out_of_scope", ["should I take a loan for solar", "write me an essay", "help with my homework", "what do you think about politics",
                                      "can I sue my landlord", "is bitcoin a good investment"]),
]


def generate(n_per_template: int = 4, seed: int = 20261023) -> list[dict]:
    rnd = random.Random(seed)
    rows = []
    for intent, reason, temps in TEMPLATES:
        for t in temps:
            for _ in range(n_per_template):
                text = t.format(p=rnd.choice(PRODUCTS), pl=rnd.choice(PLACES), n=rnd.choice(NAMES), d=rnd.choice(DAYS))
                if rnd.random() < 0.25:
                    text = text.lower()
                if rnd.random() < 0.15:
                    text = rnd.choice(["Hi, ", "Dumela, ", "Morning. ", "Sharp, "]) + text
                rows.append({"text": text, "intent": intent, "needs_person": reason is not None, "reason": reason})
    rnd.shuffle(rows)
    return rows


def main() -> int:
    rows = generate()
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} synthetic training rows to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
