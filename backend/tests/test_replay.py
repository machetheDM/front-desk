"""The replay reads real exports and never writes a customer's raw personal details out."""

from __future__ import annotations

import pathlib

import pytest

from frontdesk import replay, services
from frontdesk.tenancy import DEMO_TENANT_ID

EXPORT = """14/10/2026, 21:03 - Lerato: Hi
14/10/2026, 21:03 - Lerato: how much is the 3kW backup? my ID is 8501015800088
14/10/2026, 21:10 - Bushveld Solar: Thanks, we'll reply tomorrow
15/10/2026, 07:45 - +27 82 555 0101: Do you install in Mankweng?
continued on a second line
"""


def test_a_whatsapp_export_becomes_one_question_per_customer_burst(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(EXPORT)
    qs = replay.read_whatsapp_export(p, "Bushveld Solar")
    assert [q.text for q in qs] == ["Hi how much is the 3kW backup? my ID is 8501015800088",
                                    "Do you install in Mankweng? continued on a second line"]
    assert qs[0].received_at.hour == 21


@pytest.mark.db
def test_replay_outputs_contain_no_raw_id_numbers(demo_db, tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text(EXPORT)
    rows = replay.run(demo_db, DEMO_TENANT_ID, replay.read_whatsapp_export(p, "Bushveld Solar"), services.build("offline"))
    out = replay.write_outputs(rows, tmp_path / "out", tenant_name="Bushveld", pipeline="offline")
    everything = (tmp_path / "out" / "replay.jsonl").read_text() + pathlib.Path(out).read_text()
    assert "8501015800088" not in everything
    assert "[ID_NUMBER]" in everything
    assert rows[0].after_hours is True
