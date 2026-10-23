"""Check your real AWS setup against docs/DEPLOY.md, step by step. Read-only.

    python -m frontdesk.deploy_check --mgmt fd-mgmt
    python -m frontdesk.deploy_check --mgmt fd-mgmt --logs fd-logs --security fd-security --dev fd-dev

Run it after any step from step 5 on (before step 5 there are no CLI credentials). One line per
check: PASS; FAIL (the step was done but something is wrong); TODO (not done yet); SKIP (no
profile given for that account yet, or an optional check you did not ask for). Each line names
the DEPLOY.md step to go back to, and the last line names the first step that needs you.

Nothing is changed in any account: every AWS call is a read. Two checks are slower and opt-in:
`--database` reads the dev database through the Data API (it wakes Aurora for a minute, a few
cents at most), and `--bedrock` sends one five-token request to Claude Haiku in Frankfurt (well
under one cent) to prove model access.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HOME, AI, GLOBAL = "af-south-1", "eu-central-1", "us-east-1"
ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY_MD = ROOT / "docs" / "DEPLOY.md"
PASS, FAIL, TODO, SKIP = "PASS", "FAIL", "TODO", "SKIP"

# Mirrors infra/lib/org/scp.ts ATTACHMENTS (backend/tests/test_deploy_check.py keeps them equal).
SCP_ATTACHMENTS = {
    "root": ["deny-unapproved-regions", "frankfurt-is-for-bedrock", "protect-audit-trail", "deny-long-lived-credentials"],
    "security": ["deny-root-and-leaving", "protect-log-archive"],
    "workloads": ["deny-root-and-leaving"],
}
TARGET_NAMES = {"root": ("ROOT", "Root"), "security": ("ORGANIZATIONAL_UNIT", "Security"), "workloads": ("ORGANIZATIONAL_UNIT", "Workloads")}
ACCOUNTS = {"frontdesk-security": "security", "frontdesk-log-archive": "logs", "frontdesk-dev": "dev", "frontdesk-prod": "prod"}
DEV_STACKS = {"FrontDesk-dev-Ai": AI, "FrontDesk-dev-Data": HOME, "FrontDesk-dev-Web": HOME, "FrontDesk-dev-Signing": HOME,
              "FrontDesk-dev-App": HOME, "FrontDesk-dev-Detection": HOME}
GITHUB_ROLES = ["frontdesk-github-deploy-dev", "frontdesk-github-pr-diff", "frontdesk-github-evals"]
GITHUB_VARIABLES = ["AWS_DEV_ACCOUNT", "AWS_DEV_DEPLOY_ROLE", "AWS_PR_DIFF_ROLE", "AWS_EVALS_ROLE", "ALERT_EMAIL"]


@dataclass
class Result:
    step: int
    check: str
    status: str
    detail: str = ""


class SignInExpired(RuntimeError):
    pass


def _code(e: Exception) -> str:
    return getattr(e, "response", {}).get("Error", {}).get("Code", "")


class Ctx:
    """Who we can ask, and what we learned so far (account ids, the org root)."""

    def __init__(self, profiles: dict[str, str | None], client: Callable[[str, str, str], Any] | None = None,
                 *, database: bool = False, bedrock: bool = False, repo: str = "machetheDM/front-desk",
                 gh: Callable[[list[str]], str | None] | None = None) -> None:
        self.profiles, self.database, self.bedrock, self.repo = profiles, database, bedrock, repo
        self._client = client or self._boto
        self._sessions: dict[str, Any] = {}
        self.gh = gh if gh is not None else _gh
        self.facts: dict[str, Any] = {}

    def has(self, who: str) -> bool:
        return bool(self.profiles.get(who))

    def _boto(self, who: str, service: str, region: str):
        import boto3

        if who not in self._sessions:
            self._sessions[who] = boto3.Session(profile_name=self.profiles[who])
        return self._sessions[who].client(service, region_name=region)

    def client(self, who: str, service: str, region: str):
        return self._client(who, service, region)


def _gh(args: list[str]) -> str | None:
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)  # nosemgrep -- fixed arguments, no shell
    return r.stdout if r.returncode == 0 else None


def _paginate(client, op: str, key: str, **kw) -> list:
    out = []
    for page in client.get_paginator(op).paginate(**kw):
        out += page.get(key, [])
    return out


def _stack(cfn, name: str) -> tuple[str, str]:
    try:
        st = cfn.describe_stacks(StackName=name)["Stacks"][0]["StackStatus"]
    except Exception as e:
        if _code(e) == "ValidationError":
            return TODO, "not deployed yet"
        raise
    if st.endswith("_COMPLETE") and "ROLLBACK" not in st and "DELETE" not in st:
        return PASS, st
    return (TODO if st.endswith("_IN_PROGRESS") else FAIL), st


def _outputs(cfn, name: str) -> dict[str, str]:
    try:
        st = cfn.describe_stacks(StackName=name)["Stacks"][0]
    except Exception:
        return {}
    return {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}


def _confirmed(sns, topic_name: str) -> tuple[str, str]:
    arn = next((t["TopicArn"] for t in _paginate(sns, "list_topics", "Topics") if t["TopicArn"].endswith(":" + topic_name)), None)
    if not arn:
        return TODO, f"topic {topic_name} not found"
    subs = _paginate(sns, "list_subscriptions_by_topic", "Subscriptions", TopicArn=arn)
    if not subs:
        return FAIL, "no e-mail subscribed"
    if any(s["SubscriptionArn"] == "PendingConfirmation" for s in subs):
        return FAIL, "open the e-mail from AWS Notifications and click Confirm subscription"
    return PASS, f"{len(subs)} confirmed"


# ----- the checks, one function per DEPLOY.md step -----

def step1(ctx: Ctx) -> list[Result]:
    summary = ctx.client("mgmt", "iam", GLOBAL).get_account_summary()["SummaryMap"]
    out = [Result(1, "root user has MFA", PASS if summary.get("AccountMFAEnabled") == 1 else FAIL,
                  "" if summary.get("AccountMFAEnabled") == 1 else "Security credentials, Assign MFA device"),
           Result(1, "root user has no access keys", PASS if summary.get("AccountAccessKeysPresent") == 0 else FAIL,
                  "" if summary.get("AccountAccessKeysPresent") == 0 else "deactivate, then delete them")]
    account = ctx.client("mgmt", "account", GLOBAL)
    missing = []
    for kind in ("BILLING", "OPERATIONS", "SECURITY"):
        try:
            account.get_alternate_contact(AlternateContactType=kind)
        except Exception as e:
            if _code(e) != "ResourceNotFoundException":
                raise
            missing.append(kind.lower())
    out.append(Result(1, "alternate contacts set", FAIL if missing else PASS, f"missing: {', '.join(missing)}" if missing else ""))
    return out


def step2(ctx: Ctx) -> list[Result]:
    st = ctx.client("mgmt", "account", GLOBAL).get_region_opt_status(RegionName=HOME)["RegionOptStatus"]
    return [Result(2, "Cape Town (af-south-1) enabled", PASS if st in ("ENABLED", "ENABLED_BY_DEFAULT") else TODO, st)]


def step3(ctx: Ctx) -> list[Result]:
    org = ctx.client("mgmt", "organizations", GLOBAL)
    try:
        o = org.describe_organization()["Organization"]
    except Exception as e:
        if _code(e) == "AWSOrganizationsNotInUseException":
            return [Result(3, "organization exists", TODO, "not created yet")]
        raise
    ctx.facts["mgmt"] = o["MasterAccountId"]
    root = org.list_roots()["Roots"][0]
    ctx.facts["root"] = root["Id"]
    scp = any(p["Type"] == "SERVICE_CONTROL_POLICY" and p["Status"] == "ENABLED" for p in root.get("PolicyTypes", []))
    return [Result(3, "organization with all features", PASS if o["FeatureSet"] == "ALL" else FAIL, o["FeatureSet"]),
            Result(3, "service control policies enabled", PASS if scp else TODO, "" if scp else "Policies, Service control policies, Enable")]


def step4(ctx: Ctx) -> list[Result]:
    instances = ctx.client("mgmt", "sso-admin", HOME).list_instances().get("Instances", [])
    return [Result(4, "IAM Identity Center enabled in Cape Town", PASS if instances else TODO,
                   "" if instances else "not found in af-south-1")]


def step5(ctx: Ctx) -> list[Result]:
    who = ctx.client("mgmt", "sts", GLOBAL).get_caller_identity()
    ctx.facts.setdefault("mgmt", who["Account"])
    sso = "AWSReservedSSO_FrontDeskAdmin_" in who["Arn"]
    out = [Result(5, "signed in through SSO as FrontDeskAdmin", PASS if sso else FAIL,
                  "" if sso else f"signed in as {who['Arn'].split(':')[-1]}: use the fd-mgmt SSO profile, not access keys")]
    if ctx.facts.get("mgmt") and who["Account"] != ctx.facts["mgmt"]:
        out.append(Result(5, "fd-mgmt is the management account", FAIL, "this profile points at a member account"))
    return out


def step6(ctx: Ctx) -> list[Result]:
    if "root" not in ctx.facts:
        return [Result(6, "trusted access for CloudTrail and Access Analyzer", TODO, "needs step 3 first")]
    enabled = {p["ServicePrincipal"] for p in _paginate(ctx.client("mgmt", "organizations", GLOBAL),
                                                        "list_aws_service_access_for_organization", "EnabledServicePrincipals")}
    # account.amazonaws.com: without it step 8 cannot enable Cape Town in the member accounts.
    need = {"cloudtrail.amazonaws.com", "access-analyzer.amazonaws.com", "sso.amazonaws.com", "account.amazonaws.com"}
    missing = sorted(need - enabled)
    return [Result(6, "Organizations trusted access", TODO if missing else PASS,
                   f"missing: {', '.join(missing)}" if missing else "")]


def step7(ctx: Ctx) -> list[Result]:
    status, detail = _stack(ctx.client("mgmt", "cloudformation", GLOBAL), "FrontDesk-Organization")
    out = [Result(7, "FrontDesk-Organization stack deployed", status, detail)]
    if status != PASS:
        return out
    org = ctx.client("mgmt", "organizations", GLOBAL)
    accounts = {a["Name"]: a for a in _paginate(org, "list_accounts", "Accounts")}
    ids = {}
    for name, key in ACCOUNTS.items():
        a = accounts.get(name)
        state = (a or {}).get("State") or (a or {}).get("Status")
        if a and state == "ACTIVE":
            ids[key] = a["Id"]
        out.append(Result(7, f"account {name} active", PASS if state == "ACTIVE" else TODO, state or "missing"))
    ctx.facts["accounts"] = ids
    policies = {p["Name"]: p["Id"] for p in _paginate(org, "list_policies", "Policies", Filter="SERVICE_CONTROL_POLICY")}
    expected: dict[str, set] = {}
    for target, names in SCP_ATTACHMENTS.items():
        for n in names:
            expected.setdefault(f"frontdesk-{n}", set()).add(TARGET_NAMES[target])
    for name, want in sorted(expected.items()):
        pid = policies.get(name)
        if not pid:
            out.append(Result(7, f"SCP {name} attached", FAIL, "policy missing"))
            continue
        got = {(t["Type"], t["Name"]) for t in _paginate(org, "list_targets_for_policy", "Targets", PolicyId=pid)}
        out.append(Result(7, f"SCP {name} attached", PASS if want <= got else FAIL,
                          "" if want <= got else f"attached to {sorted(n for _, n in got) or 'nothing'}, expected {sorted(n for _, n in want)}"))
    return out


def step8(ctx: Ctx) -> list[Result]:
    ids = ctx.facts.get("accounts")
    if not ids:
        return [Result(8, "Cape Town enabled in member accounts", TODO, "needs step 7 first")]
    account = ctx.client("mgmt", "account", GLOBAL)
    out = []
    for key, aid in sorted(ids.items()):
        st = account.get_region_opt_status(AccountId=aid, RegionName=HOME)["RegionOptStatus"]
        out.append(Result(8, f"Cape Town enabled in {key}", PASS if st in ("ENABLED", "ENABLED_BY_DEFAULT") else TODO, st))
    return out


def step10(ctx: Ctx) -> list[Result]:
    ids = ctx.facts.get("accounts", {})
    out = []
    for key, profile_key in (("security", "security"), ("logs", "logs"), ("dev", "dev"), ("prod", "prod")):
        if not ctx.has(profile_key):
            out.append(Result(10, f"profile for the {key} account", SKIP, f"no --{key} profile given"))
            continue
        acct = ctx.client(profile_key, "sts", HOME).get_caller_identity()["Account"]
        want = ids.get(key)
        ok = want is None or acct == want
        out.append(Result(10, f"profile for the {key} account", PASS if ok else FAIL,
                          "" if ok else f"--{key} profile points at {acct}, the {key} account is {want}: run aws configure sso again"))
    return out


def step11(ctx: Ctx) -> list[Result]:
    mgmt = ctx.facts.get("mgmt")
    if not mgmt:
        return [Result(11, "monthly budget with alerts", TODO, "needs step 5 first")]
    budgets = ctx.client("mgmt", "budgets", GLOBAL)
    names = {b["BudgetName"]: b for b in budgets.describe_budgets(AccountId=mgmt).get("Budgets", [])}
    b = names.get("frontdesk-monthly-total")
    if not b:
        return [Result(11, "monthly budget with alerts", TODO, "frontdesk-monthly-total not found")]
    n = len(budgets.describe_notifications_for_budget(AccountId=mgmt, BudgetName="frontdesk-monthly-total").get("Notifications", []))
    return [Result(11, "monthly budget with alerts", PASS if n >= 4 else FAIL,
                   f"limit {b['BudgetLimit']['Amount']} {b['BudgetLimit']['Unit']}, {n} alerts")]


def step12(ctx: Ctx) -> list[Result]:
    out = []
    if ctx.has("logs"):
        status, detail = _stack(ctx.client("logs", "cloudformation", HOME), "FrontDesk-LogArchive")
        out.append(Result(12, "FrontDesk-LogArchive stack deployed", status, detail))
    else:
        out.append(Result(12, "FrontDesk-LogArchive stack deployed", SKIP, "no --logs profile given"))
    ct = ctx.client("mgmt", "cloudtrail", GLOBAL)
    trails = ct.describe_trails(trailNameList=["frontdesk-org-trail"]).get("trailList", [])
    if not trails:
        return out + [Result(12, "organization trail logging", TODO, "frontdesk-org-trail not found")]
    t = trails[0]
    shape = [k for k in ("IsOrganizationTrail", "IsMultiRegionTrail", "LogFileValidationEnabled") if not t.get(k)]
    logging = ct.get_trail_status(Name="frontdesk-org-trail").get("IsLogging")
    out.append(Result(12, "organization trail logging", PASS if logging and not shape else FAIL,
                      "" if logging and not shape else ("not logging " if not logging else "") + (f"missing {', '.join(shape)}" if shape else "")))
    status, detail = _confirmed(ctx.client("mgmt", "sns", GLOBAL), "frontdesk-security-alerts")
    out.append(Result(12, "security alert e-mail confirmed", status, detail))
    return out


def step13(ctx: Ctx) -> list[Result]:
    sec = ctx.facts.get("accounts", {}).get("security")
    out = []
    if sec:
        admins = {a["Id"] for a in _paginate(ctx.client("mgmt", "organizations", GLOBAL), "list_delegated_administrators",
                                             "DelegatedAdministrators", ServicePrincipal="access-analyzer.amazonaws.com")}
        out.append(Result(13, "security account delegated for Access Analyzer", PASS if sec in admins else TODO, ""))
    if not ctx.has("security"):
        return out + [Result(13, "organization analyzer active", SKIP, "no --security profile given")]
    analyzers = ctx.client("security", "accessanalyzer", HOME).list_analyzers(type="ORGANIZATION").get("analyzers", [])
    a = next((x for x in analyzers if x["name"] == "frontdesk-org-external-access"), None)
    out.append(Result(13, "organization analyzer active", PASS if a and a["status"] == "ACTIVE" else TODO,
                      a["status"] if a else "frontdesk-org-external-access not found"))
    status, detail = _confirmed(ctx.client("security", "sns", HOME), "frontdesk-access-findings")
    out.append(Result(13, "access findings e-mail confirmed", status, detail))
    return out


def step14(ctx: Ctx) -> list[Result]:
    path = ROOT / "docs" / "evidence" / "region-check.json"
    return [Result(14, "region check recorded", PASS if path.exists() else TODO,
                   "" if path.exists() else "run scripts/check-regions.ps1 and commit the file it writes")]


def step15(ctx: Ctx) -> list[Result]:
    if not ctx.bedrock or not ctx.has("dev"):
        return [Result(15, "Claude Haiku answers in Frankfurt", SKIP, "add --bedrock (and --dev) to test model access")]
    from .services import DEFAULT_MODEL_SMALL

    try:
        ctx.client("dev", "bedrock-runtime", AI).converse(
            modelId=DEFAULT_MODEL_SMALL, messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
            inferenceConfig={"maxTokens": 5})
    except Exception as e:
        if _code(e) in ("AccessDeniedException", "ValidationException", "ResourceNotFoundException"):
            return [Result(15, "Claude Haiku answers in Frankfurt", FAIL, f"{_code(e)}: request model access in the Bedrock console")]
        raise
    return [Result(15, "Claude Haiku answers in Frankfurt", PASS, DEFAULT_MODEL_SMALL)]


def step16(ctx: Ctx) -> list[Result]:
    out = []
    if ctx.has("dev"):
        status, detail = _stack(ctx.client("dev", "cloudformation", HOME), "FrontDesk-dev-GithubOidc")
        out.append(Result(16, "FrontDesk-dev-GithubOidc stack deployed", status, detail))
        if status == PASS:
            iam = ctx.client("dev", "iam", GLOBAL)
            for role in GITHUB_ROLES:
                try:
                    iam.get_role(RoleName=role)
                    out.append(Result(16, f"role {role}", PASS))
                except Exception as e:
                    if _code(e) != "NoSuchEntity":
                        raise
                    out.append(Result(16, f"role {role}", FAIL, "missing"))
    else:
        out.append(Result(16, "FrontDesk-dev-GithubOidc stack deployed", SKIP, "no --dev profile given"))
    raw = ctx.gh(["variable", "list", "--repo", ctx.repo, "--json", "name"])
    if raw is None:
        return out + [Result(16, "GitHub variables and branch protection", SKIP, "gh not installed or not signed in (gh auth login)")]
    have = {v["name"] for v in json.loads(raw or "[]")}
    missing = [v for v in GITHUB_VARIABLES if v not in have]
    out.append(Result(16, "GitHub repository variables", FAIL if missing else PASS, f"missing: {', '.join(missing)}" if missing else ""))
    prot = ctx.gh(["api", f"repos/{ctx.repo}/branches/main/protection", "--jq", ".required_status_checks.contexts"])
    contexts = json.loads(prot) if prot else []
    out.append(Result(16, "main requires ci-passed", PASS if "ci-passed" in contexts else TODO,
                      "" if "ci-passed" in contexts else "branch protection not set"))
    return out


def step17(ctx: Ctx) -> list[Result]:
    if not ctx.has("dev"):
        return [Result(17, "dev stacks deployed", SKIP, "no --dev profile given")]
    out = []
    for name, region in DEV_STACKS.items():
        status, detail = _stack(ctx.client("dev", "cloudformation", region), name)
        out.append(Result(17, f"{name} deployed", status, detail))
    if any(r.status != PASS for r in out):
        return out
    web = _outputs(ctx.client("dev", "cloudformation", HOME), "FrontDesk-dev-Web").get("WebDomain")
    if web:
        out.append(_inbox_headers(f"https://{web}/inbox/"))
    if not ctx.database:
        return out + [Result(17, "dev database migrated and demo tenant loaded", SKIP, "add --database (wakes Aurora for a minute)")]
    out += _database_checks(ctx)
    return out


def _inbox_headers(url: str) -> Result:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=15) as r:  # nosemgrep -- our own CloudFront URL
            csp, hsts = r.headers.get("content-security-policy", ""), r.headers.get("strict-transport-security", "")
            ok = r.status == 200 and "script-src 'self'" in csp and "max-age" in hsts
            return Result(17, "inbox served with CSP and HSTS", PASS if ok else FAIL, f"HTTP {r.status}" + ("" if ok else ", security headers missing"))
    except Exception as e:  # noqa: BLE001 -- report, do not stop
        return Result(17, "inbox served with CSP and HSTS", FAIL, f"{type(e).__name__}: {e}")


def _database_checks(ctx: Ctx) -> list[Result]:
    from . import restore_check
    from .db.dataapi import DataApiDatabase
    from .db.migrate import data_api_runner
    from .tenancy import DEMO_TENANT_ID

    o = _outputs(ctx.client("dev", "cloudformation", HOME), "FrontDesk-dev-Data")
    rds = ctx.client("dev", "rds-data", HOME)
    _, query = data_api_runner(o["ClusterArn"], o["AdminSecretArn"], "frontdesk", client=rds)
    try:
        checks = restore_check.admin_checks(query)
    except Exception as e:  # noqa: BLE001 -- most often: the migrate Lambda has not run
        return [Result(17, "dev database migrated", FAIL, f"{type(e).__name__}: invoke frontdesk-dev-migrate (step 17)")]
    checks += restore_check.app_checks(DataApiDatabase(o["ClusterArn"], o["AppSecretArn"], "frontdesk", client=rds), [DEMO_TENANT_ID], None)
    return [Result(17, f"database: {c['check']}", PASS if c["ok"] else FAIL, "" if c["ok"] else c["detail"]) for c in checks]


def step18(ctx: Ctx) -> list[Result]:
    if not ctx.has("dev"):
        return [Result(18, "WhatsApp number linked", SKIP, "no --dev profile given")]
    linked = ctx.client("dev", "socialmessaging", HOME).list_linked_whatsapp_business_accounts().get("linkedAccounts", [])
    out = [Result(18, "WhatsApp business account linked", PASS if linked else TODO, f"{len(linked)} linked" if linked else "after Meta verification")]
    params = ctx.client("dev", "ssm", HOME).get_parameters_by_path(Path="/frontdesk/dev/routing/whatsapp", Recursive=True).get("Parameters", [])
    out.append(Result(18, "WhatsApp number mapped to a tenant", PASS if params else TODO, "" if params else "admin map-whatsapp"))
    return out


def step19(ctx: Ctx) -> list[Result]:
    if not ctx.has("prod"):
        return [Result(19, "prod stacks deployed", SKIP, "no --prod profile given (fd-prod-ro is enough)")]
    out = []
    for name, region in {n.replace("-dev-", "-prod-"): r for n, r in DEV_STACKS.items()}.items():
        status, detail = _stack(ctx.client("prod", "cloudformation", region), name)
        out.append(Result(19, f"{name} deployed", status, detail))
    return out


STEPS: list[tuple[int, str, Callable[[Ctx], list[Result]]]] = [
    (1, "mgmt", step1), (2, "mgmt", step2), (3, "mgmt", step3), (4, "mgmt", step4), (5, "mgmt", step5), (6, "mgmt", step6),
    (7, "mgmt", step7), (8, "mgmt", step8), (10, "mgmt", step10), (11, "mgmt", step11), (12, "mgmt", step12), (13, "mgmt", step13),
    (14, "", step14), (15, "dev", step15), (16, "", step16), (17, "dev", step17), (18, "dev", step18), (19, "prod", step19),
]
SIGN_IN_ERRORS = ("UnauthorizedSSOTokenError", "SSOTokenLoadError", "TokenRetrievalError", "SSOError", "ExpiredToken", "ExpiredTokenException")


def run(ctx: Ctx) -> list[Result]:
    """Every step in order. Steps that need a profile you did not give report SKIP themselves."""
    results: list[Result] = []
    for step, who, fn in STEPS:
        try:
            results += fn(ctx)
        except Exception as e:  # noqa: BLE001 -- one broken check must not hide the others
            if type(e).__name__ in SIGN_IN_ERRORS or _code(e) in SIGN_IN_ERRORS:
                profile = ctx.profiles.get(who or "mgmt")
                raise SignInExpired(f"your SSO sign-in for {profile} has expired: aws sso login --profile {profile}") from e
            if _code(e) in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
                results.append(Result(step, "permission to read", FAIL, f"{_code(e)}: is FrontDeskAdmin assigned on this account (step 10)?"))
            else:
                results.append(Result(step, "check ran", FAIL, f"{type(e).__name__}: {str(e)[:160]}"))
    return results


def step_titles() -> dict[int, str]:
    if not DEPLOY_MD.exists():
        return {}
    # "Secure the root user (console, management account)" -> "Secure the root user"
    return {int(n): re.sub(r"\s*\([^)]*\)\s*$", "", t.strip())
            for n, t in re.findall(r"^## (\d+)\. (.+)$", DEPLOY_MD.read_text(encoding="utf-8"), flags=re.M)}


def report(results: list[Result]) -> str:
    titles = step_titles()
    lines = [f"Front Desk deploy check (read-only), {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC", "",
             f"{'step':>4}  {'result':<6}  {'check':<52}  detail"]
    for r in results:
        lines.append(f"{r.step:>4}  {r.status:<6}  {r.check[:52]:<52}  {r.detail}")
    first = next((r for r in results if r.status in (FAIL, TODO)), None)
    lines.append("")
    if first is None:
        lines.append("Everything checked passed. Steps marked SKIP need a profile or an option to be checked.")
    else:
        lines.append(f"Next: DEPLOY.md step {first.step}" + (f" ({titles[first.step]})" if first.step in titles else "") + f": {first.check}.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for who, example in (("mgmt", "fd-mgmt"), ("logs", "fd-logs"), ("security", "fd-security"), ("dev", "fd-dev"), ("prod", "fd-prod-ro")):
        ap.add_argument(f"--{who}", metavar="PROFILE", help=f"AWS CLI profile for this account, e.g. {example}")
    ap.add_argument("--database", action="store_true", help="also check the dev database (wakes Aurora)")
    ap.add_argument("--bedrock", action="store_true", help="also send one tiny request to Claude to prove model access")
    ap.add_argument("--repo", default="machetheDM/front-desk")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    profiles = {k: getattr(a, k) for k in ("mgmt", "logs", "security", "dev", "prod")}
    if not profiles["mgmt"]:
        ap.error("give at least --mgmt fd-mgmt (DEPLOY.md step 5 creates it)")
    try:
        results = run(Ctx(profiles, database=a.database, bedrock=a.bedrock, repo=a.repo))
    except SignInExpired as e:
        print(str(e), file=sys.stderr)
        return 2
    print(report(results))
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
