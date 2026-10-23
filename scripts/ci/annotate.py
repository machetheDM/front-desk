"""Turn test and scanner output into GitHub annotations (::error lines), so a failure shows the
test or finding itself in the pull request, not just "exit code 1".

    python scripts/ci/annotate.py junit report.xml
    python scripts/ci/annotate.py semgrep semgrep.json
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET  # nosemgrep -- (parses our own pytest output)


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:900]


def junit(path: str) -> None:
    for case in ET.parse(path).getroot().iter("testcase"):  # nosemgrep -- our own pytest output, not untrusted XML
        for bad in list(case.findall("failure")) + list(case.findall("error")):
            name = f"{case.get('classname')}.{case.get('name')}"
            print(f"::error file={case.get('file') or 'backend/tests'},title={esc(name)}::{esc((bad.get('message') or '') + ' ' + (bad.text or '')[-600:])}")


def semgrep(path: str) -> None:
    for r in json.load(open(path)).get("results", [])[:40]:
        level = "error" if r["extra"]["severity"] == "ERROR" else "warning"
        print(f"::{level} file={r['path']},line={r['start']['line']},title={esc(r['check_id'].split('.')[-1])}::{esc(r['extra']['message'])}")


def jest(path: str) -> None:
    for suite in json.load(open(path))["testResults"]:
        for t in suite["assertionResults"]:
            if t["status"] == "failed":
                msg = " ".join(t.get("failureMessages") or [])[:600]
                print(f"::error file={suite['name'].split('/infra/')[-1] if '/infra/' in suite['name'] else suite['name']},title={esc(t['fullName'])}::{esc(msg)}")
        if suite.get("status") == "failed" and not suite["assertionResults"]:
            print(f"::error file={suite['name']},title=suite failed to run::{esc(suite.get('message', '')[:600])}")


def trivy(path: str) -> None:
    for res in json.load(open(path)).get("Results", []) or []:
        for v in (res.get("Vulnerabilities") or [])[:20]:
            print(f"::error file={res['Target']},title={esc(v['VulnerabilityID'])}::{esc(v['PkgName'] + ' ' + v.get('InstalledVersion', '') + ' -> ' + v.get('FixedVersion', '') + ': ' + v.get('Title', ''))}")
        for m in (res.get("Misconfigurations") or [])[:20]:
            print(f"::error file={res['Target']},title={esc(m['ID'])}::{esc(m.get('Title', ''))}")
        for x in (res.get("Secrets") or [])[:20]:
            print(f"::error file={res['Target']},line={x.get('StartLine', 1)},title={esc(x['RuleID'])}::{esc(x.get('Title', ''))}")


if __name__ == "__main__":
    {"junit": junit, "semgrep": semgrep, "trivy": trivy, "jest": jest}[sys.argv[1]](sys.argv[2])
