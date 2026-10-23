"""Consent records (POPIA s69). Two purposes, stored separately, append-only.

- `service`: the customer may be answered. Recorded when a customer starts a conversation
  (they asked us); withdrawn by STOP.
- `marketing`: the customer may be sent promotions. Never inferred. Only an explicit opt-in
  creates it, and nothing in Front Desk sends marketing without it (`marketing_allowed`).
"""

from __future__ import annotations

from .db import Transaction


def record(tx: Transaction, customer_id: str, purpose: str, granted: bool, source: str, evidence: str = "") -> None:
    tx.execute(
        """INSERT INTO app.consents (tenant_id, customer_id, purpose, granted, source, evidence)
           VALUES (app.current_tenant(), :c, :p, :g, :s, :e)""",
        {"c": str(customer_id), "p": purpose, "g": granted, "s": source, "e": evidence},
    )


def current(tx: Transaction, customer_id: str, purpose: str) -> bool | None:
    row = tx.one(
        """SELECT granted FROM app.consents WHERE customer_id = :c AND purpose = :p
           ORDER BY consent_id DESC LIMIT 1""",
        {"c": str(customer_id), "p": purpose},
    )
    return None if row is None else bool(row["granted"])


def may_answer(tx: Transaction, customer_id: str) -> bool:
    # No record yet means a new customer who just wrote to us: answering is what they asked for.
    return current(tx, customer_id, "service") is not False


def marketing_allowed(tx: Transaction, customer_id: str) -> bool:
    return current(tx, customer_id, "marketing") is True


def opt_out(tx: Transaction, customer_id: str, source: str) -> None:
    record(tx, customer_id, "service", False, source, "customer sent STOP")
    record(tx, customer_id, "marketing", False, source, "customer sent STOP")
