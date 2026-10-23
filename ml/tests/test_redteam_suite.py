"""The red-team suite is well-formed and every case maps to OWASP LLM Top 10 and MITRE ATLAS."""

import re

from frontdesk_ml import redteam

OWASP_2025 = {f"LLM{n:02d}" for n in range(1, 11)}


def test_every_case_has_a_unique_id_a_mapping_and_a_success_condition():
    cases = redteam.load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) >= 40
    for c in cases:
        assert c["owasp"] in OWASP_2025, c["id"]
        assert re.fullmatch(r"AML\.T\d{4}(\.\d{3})?", c["atlas"]), c["id"]
        assert c.get("success_if"), c["id"]


def test_the_suite_covers_the_threats_named_in_the_threat_model():
    cats = {c["category"] for c in redteam.load_cases()}
    for needed in ("direct_injection", "indirect_injection", "prompt_leak", "commitment", "tool_abuse", "cross_tenant", "pii_to_model",
                   "output_handling"):
        assert needed in cats


def test_ai_security_maps_all_ten_owasp_risks_and_every_tag_the_suite_uses():
    import pathlib
    import re

    page = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "AI-SECURITY.md").read_text()
    mapped_owasp = set(re.findall(r"\*\*(LLM\d\d) ", page))
    assert mapped_owasp == {f"LLM{n:02d}" for n in range(1, 11)}
    mapped_atlas = set(re.findall(r"^\| (AML\.T\d{4}(?:\.\d{3})?) ", page, flags=re.M))
    cases = redteam.load_cases()
    assert {c["owasp"] for c in cases} <= mapped_owasp
    assert {c["atlas"] for c in cases} <= mapped_atlas, {c["atlas"] for c in cases} - mapped_atlas
    # Every red-team id the page cites as evidence exists, including both ends of a range.
    ids = {c["id"] for c in cases}
    cited = set(re.findall(r"\brt\d\d\b", page))
    assert cited <= ids, cited - ids


def test_the_promptfoo_export_skips_cases_that_need_database_fixtures(tmp_path):
    import yaml

    out = tmp_path / "pf.yaml"
    redteam.export_promptfoo(out, redteam.load_cases())
    cfg = yaml.safe_load(out.read_text())
    assert cfg["providers"][0]["id"] == "https"
    assert not any("rt21" in t["description"] for t in cfg["tests"])  # needs the rival tenant
    assert len(cfg["tests"]) >= 30
