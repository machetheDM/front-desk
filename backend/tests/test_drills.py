"""Drills run on every pull request, so a recorded drill cannot quietly stop being true."""

from __future__ import annotations

import pytest

from frontdesk import drills

pytestmark = pytest.mark.db


def test_the_model_down_drill_passes_every_step_against_the_demo_tenant(demo_db):
    steps = drills.model_down(demo_db)
    failed = [(s.name, s.observed) for s in steps if not s.passed]
    assert failed == []
    assert len(steps) >= 9


def test_the_drill_record_says_how_many_steps_passed(demo_db):
    text = drills.record(drills.model_down(demo_db), "local Postgres")
    assert "steps passed" in text and "**NO**" not in text
