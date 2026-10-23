"""DORA metrics from this repository's own deploy history.

    python tools/dora/dora.py --repo machetheDM/front-desk --workflow deploy-prod.yml --weeks 12

Reads completed runs of a deploy workflow through the GitHub API (GITHUB_TOKEN, read-only) and
reports the four DORA keys:

- deployment frequency: successful deploys per week;
- lead time for changes: median time from a commit to the successful deploy that shipped it;
- change failure rate: failed deploy runs (including canary rollbacks, which fail the run) over all;
- time to restore: median time from a failed deploy to the next successful one.

Standard library only, so the scheduled workflow (`.github/workflows/dora.yml`) installs nothing.
The arithmetic is in `metrics()`, tested on synthetic histories in backend/tests/test_dora.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"


@dataclass
class Deploy:
    sha: str
    finished_at: dt.datetime
    ok: bool
    commit_times: list[dt.datetime] = field(default_factory=list)


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def released_ref(run: dict) -> str:
    """deploy-prod names its runs 'deploy-prod <ref>'; other deploys release their head commit."""
    title = run.get("display_title") or ""
    return title.split(" ", 1)[1].strip() if title.startswith("deploy-prod ") else run["head_sha"]


def metrics(deploys: list[Deploy], weeks: float) -> dict:
    deploys = sorted(deploys, key=lambda d: d.finished_at)
    good = [d for d in deploys if d.ok]
    lead = [(d.finished_at - c).total_seconds() / 3600 for d in good for c in d.commit_times]
    restore = []
    for i, d in enumerate(deploys):
        if not d.ok:
            nxt = next((x for x in deploys[i + 1:] if x.ok), None)
            if nxt:
                restore.append((nxt.finished_at - d.finished_at).total_seconds() / 3600)
    return {
        "deploys": len(deploys), "successful": len(good), "weeks": weeks,
        "deployment_frequency_per_week": round(len(good) / weeks, 2) if weeks else 0.0,
        "lead_time_hours_median": round(statistics.median(lead), 2) if lead else None,
        "change_failure_rate": round((len(deploys) - len(good)) / len(deploys), 3) if deploys else None,
        "time_to_restore_hours_median": round(statistics.median(restore), 2) if restore else None,
    }


def _get(path: str, token: str | None) -> dict:
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                                                      **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:  # nosemgrep -- fixed GitHub API host
        return json.load(r)


def collect(repo: str, workflow: str, weeks: int, token: str | None) -> list[Deploy]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(weeks=weeks)
    runs = _get(f"/repos/{repo}/actions/workflows/{workflow}/runs?status=completed&per_page=100", token)["workflow_runs"]
    runs = sorted((r for r in runs if _ts(r["updated_at"]) >= since and r["conclusion"] in ("success", "failure")),
                  key=lambda r: r["updated_at"])
    out, previous = [], None
    for r in runs:
        sha = _get(f"/repos/{repo}/commits/{released_ref(r)}", token)["sha"]
        times = []
        if r["conclusion"] == "success":
            if previous:
                cmp = _get(f"/repos/{repo}/compare/{previous}...{sha}", token)
                times = [_ts(c["commit"]["committer"]["date"]) for c in cmp.get("commits", [])]
            else:
                times = [_ts(_get(f"/repos/{repo}/commits/{sha}", token)["commit"]["committer"]["date"])]
            previous = sha
        out.append(Deploy(sha, _ts(r["updated_at"]), r["conclusion"] == "success", times))
    return out


def summary(m: dict, workflow: str) -> str:
    def v(x, unit=""):
        return "n/a" if x is None else f"{x}{unit}"
    return "\n".join([
        f"### DORA metrics: `{workflow}`, last {m['weeks']} weeks", "",
        "| Key | Value |", "|---|---|",
        f"| Deployment frequency | {m['deployment_frequency_per_week']} successful deploys a week ({m['successful']} of {m['deploys']}) |",
        f"| Lead time for changes (median) | {v(m['lead_time_hours_median'], ' h')} |",
        f"| Change failure rate | {v(m['change_failure_rate'])} |",
        f"| Time to restore (median) | {v(m['time_to_restore_hours_median'], ' h')} |",
        "" if m["deploys"] else "\nNo deployments in this window yet: the deploy workflows need the AWS accounts (docs/DEPLOY.md).",
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--workflow", default="deploy-prod.yml")
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--json", help="also write the metrics here")
    a = ap.parse_args(argv)
    m = metrics(collect(a.repo, a.workflow, a.weeks, os.environ.get("GITHUB_TOKEN")), a.weeks)
    print(summary(m, a.workflow))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(m, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
