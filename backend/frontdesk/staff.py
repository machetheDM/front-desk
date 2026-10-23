"""Cutting off a staff member at once: the first containment step for a stolen inbox session.

Cognito access and ID tokens cannot be recalled before they expire (up to 60 minutes), and
API Gateway accepts any unexpired token it can verify. What makes the cut-off immediate is the
check every inbox request already makes: the token's user must be an *active* staff row of that
tenant (`inbox_api._staff`). Setting `active = false` therefore refuses the very next request,
whatever tokens the attacker holds; the Cognito sign-out and disable then stop new tokens.

    python -m frontdesk.admin deactivate-staff --profile fd-dev --tenant <uuid> --email pieter@... --pool <pool id> \\
        --reason "INC-... phished session" --by "Machethe DM" --dry-run
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from . import audit
from .db import Transaction
from .write_guard import WritePlan


def deactivate_plan(tenant_id: uuid.UUID, email: str, reason: str, by: str, cognito: Callable[[str], None] | None = None) -> WritePlan:
    def precondition(tx: Transaction) -> str | None:
        row = tx.one("SELECT active FROM app.staff WHERE email = :e", {"e": email})
        if row is None:
            return "no staff member with that e-mail in this tenant"
        return None if row["active"] else "already inactive"

    def apply(tx: Transaction) -> dict:
        staff_id = tx.scalar("UPDATE app.staff SET active = false WHERE email = :e RETURNING staff_id::text", {"e": email})
        audit.record(tx, actor_type="operator", actor_id=by, action="write", resource_type="staff_access",
                     resource_id=staff_id, detail={"active": False, "reason": reason})
        if cognito:
            cognito(email)  # global sign-out and disable: no new tokens from now on
        return {"deactivated": email, "staff_id": staff_id, "cognito": "signed out and disabled" if cognito else "not called"}

    return WritePlan(f"cut off {email} now", tenant_id,
                     ["staff row set inactive: the next inbox request with any token is refused",
                      "Cognito: global sign-out and disable (no new tokens)", f"reason: {reason}"], precondition, apply)
