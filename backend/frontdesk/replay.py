"""The replay: answer last month's real customer questions offline and score how it would
have done. This is the first sellable thing (docs/PLAN.md).

Input is a CSV (received_at, question[, customer_name, channel_address]) or a WhatsApp chat
export (.txt). Nothing is sent to anyone. Output is redacted: the JSONL and the owner report
never contain the customer's raw text, only the redacted version.

    python -m frontdesk.replay --dsn ... --tenant demo --input demo/bushveld/last-month.synthetic.csv --out reports/replay
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import json
import pathlib
import re
import uuid
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from .agent.state_machine import Agent, Deps, Outcome, Turn, is_office_hours, office_hours
from .db import Database
from .tenancy import DEMO_TENANT_ID

SAST = ZoneInfo("Africa/Johannesburg")


@dataclasses.dataclass
class Question:
    id: str
    text: str
    received_at: dt.datetime
    customer_name: str | None = None
    channel_address: str | None = None
    labels: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ReplayRow:
    question: Question
    outcome: Outcome
    after_hours: bool

    def public(self) -> dict:
        o = self.outcome
        return {
            "id": self.question.id, "received_at": self.question.received_at.isoformat(), "after_hours": self.after_hours,
            "question_redacted": o.redacted.text, "redactions": o.redacted.kinds, "action": o.action, "reply": o.reply,
            "handoff_reason": o.handoff_reason, "assigned_to": (o.assigned_staff or {}).get("display_name"),
            "intent": o.intent, "citations": [c.model_dump() for c in o.citations], "route": o.route, "model": o.model_id,
            "latency_ms": round(o.latency_ms, 1), "trace": o.trace, "validation_problems": o.validation_problems,
            "tokens": dataclasses.asdict(o.usage),
        }


# ----- input parsing -----

def _parse_time(s: str) -> dt.datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%Y, %H:%M"):
        try:
            t = dt.datetime.strptime(s, fmt)
            return t if t.tzinfo else t.replace(tzinfo=SAST)
        except ValueError:
            continue
    raise ValueError(f"unrecognised time: {s!r}")


def read_csv(path: pathlib.Path) -> list[Question]:
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), 1):
            out.append(Question(row.get("id") or f"r{i:04d}", row["question"], _parse_time(row["received_at"]),
                                row.get("customer_name") or None, row.get("channel_address") or None))
    return out


WA_LINE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}),? (\d{1,2}:\d{2})(?:\s?[ap]m)? - ([^:]+): (.*)$", re.I)


def read_whatsapp_export(path: pathlib.Path, business_name: str) -> list[Question]:
    """WhatsApp 'Export chat' text. Messages by anyone other than the business are customer
    messages; a customer's consecutive lines within two minutes are one question."""
    out: list[Question] = []
    last: Question | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = WA_LINE.match(line)
        if not m:
            if last and line.strip():
                last.text += " " + line.strip()
            continue
        date, time_, who, text = m.groups()
        if who.strip() == business_name or text.strip() in ("<Media omitted>", "This message was deleted"):
            last = None
            continue
        day, month, year = date.split("/")
        year = year if len(year) == 4 else "20" + year
        t = _parse_time(f"{int(day):02d}/{int(month):02d}/{year} {time_}")
        if last and last.customer_name == who.strip() and (t - last.received_at) <= dt.timedelta(minutes=2):
            last.text += " " + text
            continue
        last = Question(f"w{len(out) + 1:04d}", text, t, who.strip())
        out.append(last)
    return out


# ----- running -----

def run(db: Database, tenant_id: uuid.UUID, questions: Iterable[Question], deps: Deps) -> list[ReplayRow]:
    agent = Agent(deps)
    rows = []
    for q in questions:
        with db.transaction(tenant_id) as tx:
            hours = office_hours(tx)
            turn = Turn(text=q.text, channel="replay", channel_address=q.channel_address, customer_name=q.customer_name,
                        first_message=False, received_at=q.received_at)
            o = agent.run(tx, turn)
        rows.append(ReplayRow(q, o, not is_office_hours(q.received_at.astimezone(SAST), hours)))
    return rows


def write_outputs(rows: list[ReplayRow], out_dir: pathlib.Path, *, tenant_name: str, pipeline: str) -> pathlib.Path:
    from .report import owner_report

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "replay.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r.public(), ensure_ascii=False) + "\n")
    md = owner_report(rows, tenant_name=tenant_name, pipeline=pipeline)
    (out_dir / "owner-report.md").write_text(md, encoding="utf-8")
    return out_dir / "owner-report.md"


def main(argv: list[str] | None = None) -> int:
    from . import services
    from .db.local import LocalDatabase

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True, help="Postgres as frontdesk_app (local replay)")
    ap.add_argument("--tenant", default="demo", help="'demo' or a tenant uuid")
    ap.add_argument("--input", required=True)
    ap.add_argument("--whatsapp-business-name", help="for .txt exports: the business's own display name")
    ap.add_argument("--pipeline", choices=["offline", "bedrock"], default="offline")
    ap.add_argument("--out", default="reports/replay")
    a = ap.parse_args(argv)
    tenant = DEMO_TENANT_ID if a.tenant == "demo" else uuid.UUID(a.tenant)
    path = pathlib.Path(a.input)
    qs = read_whatsapp_export(path, a.whatsapp_business_name or "") if path.suffix == ".txt" else read_csv(path)
    db = LocalDatabase(a.dsn)
    with db.transaction(tenant) as tx:
        name = (tx.one("SELECT name FROM app.tenants") or {"name": "the business"})["name"]
    rows = run(db, tenant, qs, services.build(a.pipeline))
    report = write_outputs(rows, pathlib.Path(a.out), tenant_name=name, pipeline=a.pipeline)
    print(f"replayed {len(rows)} questions; report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
