"""The operator's switch: pause the assistant for one tenant during an incident.

While paused, every message is stored and handed to a named person with a "a person will reply"
message, exactly like degraded mode, and STOP and opt-in still work (consent is never paused).
Used when the assistant itself is the problem: it is answering wrongly at scale, a document
turns out to be poisoned, or the client asks for it to stop (docs/INCIDENT-PLAN.md step 2).

    python -m frontdesk.admin pause-assistant --tenant <uuid> --reason "INC-2026-11-02 wrong prices" --dry-run
    python -m frontdesk.admin resume-assistant --tenant <uuid> --reason "prices fixed and evals rerun" --dry-run

It lives in `app.tenant_config` (key `assistant_mode`), so it takes effect on the next message
with no deploy, and each change is written to the append-only audit log with who and why.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from . import audit
from .db import Transaction
from .write_guard import WritePlan


def paused(tx: Transaction) -> dict | None:
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'assistant_mode'")
    if not row:
        return None
    v = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
    return v if v.get("mode") == "paused" else None


def _plan(tenant_id: uuid.UUID, mode: str, reason: str, by: str) -> WritePlan:
    if len(reason.strip()) < 8:
        raise ValueError("give a reason a colleague would understand later (an incident id and a few words)")

    def precondition(tx: Transaction) -> str | None:
        now = "paused" if paused(tx) else "active"
        return None if now != mode else f"the assistant is already {mode}"

    def apply(tx: Transaction) -> dict:
        value = {"mode": mode, "reason": reason, "by": by, "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
        tx.execute("""INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), 'assistant_mode', :v::jsonb, :b)
                      ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, approved_by = EXCLUDED.approved_by, updated_at = now()""",
                   {"v": value, "b": by})
        audit.record(tx, actor_type="operator", actor_id=by, action="write", resource_type="assistant_mode",
                     resource_id=str(tenant_id), detail={"mode": mode, "reason": reason})
        return {"assistant": mode}

    what = ("every message goes to a named person; customers are told a person will reply; STOP still works"
            if mode == "paused" else "the assistant answers again from the next message")
    return WritePlan(f"set the assistant to {mode}", tenant_id, [what, f"reason: {reason}"], precondition, apply)


def pause_plan(tenant_id: uuid.UUID, reason: str, by: str) -> WritePlan:
    return _plan(tenant_id, "paused", reason, by)


def resume_plan(tenant_id: uuid.UUID, reason: str, by: str) -> WritePlan:
    return _plan(tenant_id, "active", reason, by)
