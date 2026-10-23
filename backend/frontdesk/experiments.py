"""A/B tests of prompt versions: sticky assignment, the prompt per arm, and outcomes from events.

An experiment is one row in `app.tenant_config` (key `experiment`), written only by
`python -m frontdesk.admin start-experiment`, which previews first and refuses unless the
pre-registration file in `experiments/` is approved and unchanged since it was registered
(its SHA-256 is stored with the experiment). The statistics live in `ml/frontdesk_ml/ab.py`.
SEAM(ab-live-traffic): a live run needs real customers and the Bedrock pipeline; the offline
answerer ignores the prompt, so offline the arms differ only in what is recorded.

Why assignment is by customer, not by message: a customer who gets a short answer in one
message and a long one in the next is in neither arm, and their second message is not
independent of their first. Hashing the experiment id with the customer's number gives every
customer one arm for the whole experiment, without storing an assignment table.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
import uuid

import yaml

from .db import Transaction
from .llm.prompts import CURRENT, load
from .write_guard import WritePlan

ID_RE = re.compile(r"^exp-\d{4}-\d{2}-[a-z0-9-]{3,40}$")
# The version names a file; anything else (a path, "..") is refused before it reaches the disk.
PROMPT_RE = re.compile(r"^answer\.v\d{1,3}$")


def active(tx: Transaction) -> dict | None:
    row = tx.one("SELECT value FROM app.tenant_config WHERE key = 'experiment'")
    if not row:
        return None
    cfg = row["value"] if isinstance(row["value"], dict) else json.loads(row["value"])
    return cfg if cfg.get("status") == "running" and not problems(cfg) else None


def problems(cfg: dict) -> list[str]:
    """Everything that would make the experiment unsafe or unreadable. An experiment with any
    problem is treated as not running: customers get the current prompt, never a broken arm."""
    out = []
    if not ID_RE.match(str(cfg.get("id", ""))):
        out.append("id must look like exp-YYYY-MM-short-name")
    arms = cfg.get("arms") or {}
    if set(arms) != {"control", "treatment"}:
        out.append("arms must be exactly control and treatment")
    for arm, version in arms.items():
        if not PROMPT_RE.match(str(version)):
            out.append(f"{arm} prompt must be named answer.vN")
            continue
        try:
            load(version)
        except OSError:
            out.append(f"{arm} prompt {version} does not exist")
    if arms.get("control") == arms.get("treatment"):
        out.append("control and treatment use the same prompt")
    alloc = cfg.get("allocation", 0.5)
    if not isinstance(alloc, (int, float)) or not 0.05 <= alloc <= 0.95:
        out.append("allocation must be between 0.05 and 0.95")
    if not re.fullmatch(r"[0-9a-f]{64}", str(cfg.get("prereg_sha256", ""))):
        out.append("prereg_sha256 missing: start experiments with admin start-experiment")
    return out


def assign(cfg: dict, unit: str) -> str:
    h = hashlib.sha256(f"{cfg['id']}:{unit}".encode()).digest()
    bucket = int.from_bytes(h[:8], "big") / 2**64
    return "treatment" if bucket < float(cfg.get("allocation", 0.5)) else "control"


def choose(tx: Transaction, channel_address: str | None) -> tuple[str, dict | None]:
    """The prompt version for this customer, and the record that goes into the event payload.
    No experiment, or no stable customer identity (a replay), means the current prompt."""
    cfg = active(tx)
    if cfg is None or not channel_address:
        return CURRENT, None
    arm = assign(cfg, channel_address)
    return cfg["arms"][arm], {"id": cfg["id"], "arm": arm}


def counts(tx: Transaction, experiment_id: str) -> dict[str, dict[str, int]]:
    """Per arm: customers exposed (first answered conversation in the experiment), how many of
    those needed no person afterwards (primary metric), and how many asked for a person after
    the answer (guardrail). Computed from the append-only event stream, never from a cache."""
    rows = tx.execute(
        """WITH exposed AS (
             SELECT DISTINCT ON (c.customer_id) c.customer_id, e.conversation_id,
                    e.payload->'experiment'->>'arm' AS arm, e.occurred_at
             FROM app.conversation_events e
             JOIN app.conversations c ON c.tenant_id = e.tenant_id AND c.conversation_id = e.conversation_id
             WHERE e.type = 'answer_sent' AND e.payload->'experiment'->>'id' = :x
             ORDER BY c.customer_id, e.occurred_at)
           SELECT x.arm,
                  count(*) AS exposed,
                  count(*) FILTER (WHERE NOT EXISTS (
                      SELECT 1 FROM app.conversation_events h
                      WHERE h.conversation_id = x.conversation_id AND h.occurred_at > x.occurred_at
                        AND h.type IN ('handed_over', 'degraded'))) AS resolved_without_staff,
                  count(*) FILTER (WHERE EXISTS (
                      SELECT 1 FROM app.conversation_events h
                      WHERE h.conversation_id = x.conversation_id AND h.occurred_at > x.occurred_at
                        AND h.type = 'handed_over' AND h.payload->>'reason' = 'human_requested')) AS asked_for_person
           FROM exposed x GROUP BY x.arm""",
        {"x": experiment_id})
    out = {arm: {"exposed": 0, "resolved_without_staff": 0, "asked_for_person": 0} for arm in ("control", "treatment")}
    for r in rows:
        if r["arm"] in out:
            out[r["arm"]] = {k: int(r[k]) for k in ("exposed", "resolved_without_staff", "asked_for_person")}
    return out


def _prereg(path: pathlib.Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return yaml.safe_load(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def start_plan(tenant_id: uuid.UUID, prereg_path: pathlib.Path, approved_by: str) -> WritePlan:
    """Start an experiment from its pre-registration. The precondition runs again inside the
    writing transaction, so a file edited between preview and write, or another experiment
    started meanwhile, aborts the write (write_guard)."""
    pre, sha = _prereg(prereg_path)
    cfg = {"id": pre["id"], "arms": pre["arms"], "allocation": 0.5, "status": "running",
           "prereg_sha256": sha, "prereg_path": prereg_path.name, "n_per_arm": pre["sample_size_per_arm"],
           "started_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}

    def precondition(tx: Transaction) -> str | None:
        now, now_sha = _prereg(prereg_path)
        if now_sha != sha:
            return "the pre-registration changed since the preview"
        if now.get("status") != "approved":
            return f"the pre-registration status is '{now.get('status')}', not 'approved'"
        if p := problems(cfg):
            return "; ".join(p)
        cur = tx.one("SELECT value FROM app.tenant_config WHERE key = 'experiment'")
        if cur:
            v = cur["value"] if isinstance(cur["value"], dict) else json.loads(cur["value"])
            if v.get("status") == "running":
                return f"experiment {v.get('id')} is already running; stop it first"
        return None

    def apply(tx: Transaction) -> dict:
        tx.execute("""INSERT INTO app.tenant_config (tenant_id, key, value, approved_by) VALUES (app.current_tenant(), 'experiment', :v::jsonb, :a)
                      ON CONFLICT (tenant_id, key) DO UPDATE SET value = EXCLUDED.value, approved_by = EXCLUDED.approved_by, updated_at = now()""",
                   {"v": cfg, "a": approved_by})
        return {"started": cfg["id"], "prereg_sha256": sha}

    items = [f"{arm}: {v}" for arm, v in cfg["arms"].items()] + [f"pre-registration {prereg_path.name} sha256 {sha[:12]}",
                                                                   f"{cfg['n_per_arm']} customers per arm, fixed horizon"]
    return WritePlan(f"start experiment {cfg['id']}", tenant_id, items, precondition, apply)


def stop_plan(tenant_id: uuid.UUID, experiment_id: str, reason: str) -> WritePlan:
    def precondition(tx: Transaction) -> str | None:
        cur = tx.one("SELECT value FROM app.tenant_config WHERE key = 'experiment'")
        v = (cur["value"] if isinstance(cur["value"], dict) else json.loads(cur["value"])) if cur else {}
        return None if v.get("id") == experiment_id and v.get("status") == "running" else f"{experiment_id} is not running"

    def apply(tx: Transaction) -> dict:
        tx.execute("""UPDATE app.tenant_config SET value = value || jsonb_build_object('status', 'stopped', 'stopped_reason', :r::text,
                        'stopped_at', now()::text), updated_at = now() WHERE key = 'experiment'""", {"r": reason})
        return {"stopped": experiment_id}

    return WritePlan(f"stop experiment {experiment_id}", tenant_id, [f"reason: {reason}", "every customer gets the current prompt again"],
                     precondition, apply)
