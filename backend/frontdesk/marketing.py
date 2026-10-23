"""Marketing sends, and the only way to choose who receives them (refusal 4, POPIA s69).

`audience()` returns customers whose *latest* marketing consent record is a grant. Being a
customer, being answered, or having service consent is never enough. There is no other query
in the code base that selects marketing recipients; a test asserts the exclusions.
"""

from __future__ import annotations

from .db import Transaction
from .write_guard import WritePlan

AUDIENCE_SQL = """
SELECT c.customer_id::text AS customer_id, c.channel, c.channel_address
FROM app.customers c
JOIN LATERAL (SELECT granted FROM app.consents k WHERE k.tenant_id = c.tenant_id AND k.customer_id = c.customer_id
              AND k.purpose = 'marketing' ORDER BY k.consent_id DESC LIMIT 1) latest ON true
WHERE latest.granted AND c.deleted_at IS NULL AND c.channel_address IS NOT NULL
"""


def audience(tx: Transaction) -> list[dict]:
    return tx.execute(AUDIENCE_SQL)


def plan_campaign(tenant_id, template_name: str, sender=None) -> WritePlan:
    """WhatsApp marketing must use a Meta-approved *marketing* template (and pays the
    marketing fee, docs/ARCHITECTURE.md cost table). SEAM(meta-marketing-template)."""
    def apply(tx):
        sent = 0
        for r in audience(tx):
            if sender:
                sender(r["channel_address"], template_name)
            sent += 1
        return {"template": template_name, "recipients": sent}

    def precondition(tx):
        return None if audience(tx) else "nobody has opted in to marketing"

    return WritePlan(f"send marketing template {template_name}", tenant_id, ["only customers whose latest marketing consent is a grant"], precondition, apply)
