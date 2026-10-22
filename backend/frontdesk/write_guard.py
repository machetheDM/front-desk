"""Preview before write, and re-check at the moment of writing.

Anything that writes to a real tenant (loading documents, creating staff, mapping a WhatsApp
number, deleting a customer) is built as a WritePlan. Without `--write` the plan is only
printed. With `--write`, the precondition is evaluated again *inside the transaction that
writes*, so a state change between the preview and the write (someone else loaded the same
document, the tenant turned out to be the demo tenant) aborts instead of writing on stale
assumptions.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .db import Database, Transaction


class PreconditionFailed(RuntimeError):
    pass


@dataclass
class WritePlan:
    description: str
    tenant_id: uuid.UUID
    items: list[str] = field(default_factory=list)
    precondition: Callable[[Transaction], str | None] = lambda tx: None
    apply: Callable[[Transaction], dict[str, Any]] = lambda tx: {}

    def render(self) -> str:
        lines = [f"PLAN: {self.description}", f"  tenant: {self.tenant_id}"]
        lines += [f"  - {i}" for i in self.items]
        return "\n".join(lines)


def execute(db: Database, plan: WritePlan, *, write: bool, echo: Callable[[str], None] = print) -> dict[str, Any] | None:
    echo(plan.render())
    if not write:
        with db.transaction(plan.tenant_id) as tx:
            reason = plan.precondition(tx)
        echo(f"DRY RUN: nothing written. Precondition {'would FAIL: ' + reason if reason else 'holds'}. Re-run with --write to apply.")
        return None
    with db.transaction(plan.tenant_id) as tx:
        reason = plan.precondition(tx)
        if reason:
            raise PreconditionFailed(f"aborted, nothing written: {reason}")
        result = plan.apply(tx)
    echo(f"WRITTEN: {result}")
    return result


def add_write_flags(parser) -> None:
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True, help="show the plan only (default)")
    g.add_argument("--write", action="store_true", help="apply the plan after re-checking its precondition")
