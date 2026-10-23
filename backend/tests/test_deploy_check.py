"""The deploy checker reads a real account the way DEPLOY.md describes it.

AWS is faked with botocore's Stubber, which validates every canned response against the real
service model, so a wrong field name in the checker fails here instead of on the owner's laptop.
"""

from __future__ import annotations

import datetime as dt
import http.server
import pathlib
import re
import threading

import boto3
import pytest
from botocore.exceptions import UnauthorizedSSOTokenError
from botocore.stub import Stubber

from frontdesk import deploy_check as dc

ROOT = pathlib.Path(__file__).resolve().parents[2]
MGMT, SEC, LOGS, DEV, PROD = "111111111111", "222222222222", "333333333333", "444444444444", "555555555555"
NOW = dt.datetime(2026, 10, 23, tzinfo=dt.UTC)


class Stubs:
    """One stubbed client per (profile, service, region), each with its own queue of responses."""

    def __init__(self):
        self.session = boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1")
        self.clients, self.stubbers = {}, {}

    def __call__(self, who, service, region):
        key = (who, service, region)
        if key not in self.clients:
            c = self.session.client(service, region_name=region)
            self.stubbers[key] = Stubber(c)
            self.stubbers[key].activate()
            self.clients[key] = c
        return self.clients[key]

    def ok(self, who, service, region, method, response):
        self(who, service, region)
        self.stubbers[(who, service, region)].add_response(method, response)

    def err(self, who, service, region, method, code):
        self(who, service, region)
        self.stubbers[(who, service, region)].add_client_error(method, service_error_code=code)


def ctx(stubs, **profiles):
    return dc.Ctx({"mgmt": "fd-mgmt", **profiles}, client=stubs, gh=lambda args: None)


def contact(kind):
    return {"AlternateContact": {"Name": "Owner", "Title": "Owner", "EmailAddress": "owner@example.invalid",
                                 "PhoneNumber": "+27820000000", "AlternateContactType": kind}}


def secured_management(s, mfa=1, keys=0, missing_contact=None):
    s.ok("mgmt", "iam", "us-east-1", "get_account_summary", {"SummaryMap": {"AccountMFAEnabled": mfa, "AccountAccessKeysPresent": keys}})
    for kind in ("BILLING", "OPERATIONS", "SECURITY"):
        if kind == missing_contact:
            s.err("mgmt", "account", "us-east-1", "get_alternate_contact", "ResourceNotFoundException")
        else:
            s.ok("mgmt", "account", "us-east-1", "get_alternate_contact", contact(kind))


def organization(s):
    s.ok("mgmt", "organizations", "us-east-1", "describe_organization", {"Organization": {
        "Id": "o-abcdefghij", "Arn": f"arn:aws:organizations::{MGMT}:organization/o-abcdefghij", "FeatureSet": "ALL",
        "MasterAccountId": MGMT, "MasterAccountArn": f"arn:aws:organizations::{MGMT}:account/o-abcdefghij/{MGMT}"}})
    s.ok("mgmt", "organizations", "us-east-1", "list_roots", {"Roots": [{"Id": "r-abcd", "Name": "Root",
         "Arn": f"arn:aws:organizations::{MGMT}:root/o-abcdefghij/r-abcd",
         "PolicyTypes": [{"Type": "SERVICE_CONTROL_POLICY", "Status": "ENABLED"}]}]})


def test_after_step_6_the_checker_passes_steps_1_to_6_and_names_step_7_next():
    s = Stubs()
    secured_management(s)
    s.ok("mgmt", "account", "us-east-1", "get_region_opt_status", {"RegionName": "af-south-1", "RegionOptStatus": "ENABLED"})
    organization(s)
    s.ok("mgmt", "sso-admin", "af-south-1", "list_instances", {"Instances": [{"InstanceArn": "arn:aws:sso:::instance/ssoins-1234567890abcdef"}]})
    s.ok("mgmt", "sts", "us-east-1", "get_caller_identity",
         {"UserId": "AROAEXAMPLE:me", "Account": MGMT, "Arn": f"arn:aws:sts::{MGMT}:assumed-role/AWSReservedSSO_FrontDeskAdmin_0123abcd/me"})
    s.ok("mgmt", "organizations", "us-east-1", "list_aws_service_access_for_organization", {"EnabledServicePrincipals": [
        {"ServicePrincipal": p} for p in ("cloudtrail.amazonaws.com", "access-analyzer.amazonaws.com", "sso.amazonaws.com", "account.amazonaws.com")]})
    s.err("mgmt", "cloudformation", "us-east-1", "describe_stacks", "ValidationError")
    s.ok("mgmt", "budgets", "us-east-1", "describe_budgets", {"Budgets": []})
    s.ok("mgmt", "cloudtrail", "us-east-1", "describe_trails", {"trailList": []})

    results = dc.run(ctx(s))
    by_step = {}
    for r in results:
        by_step.setdefault(r.step, set()).add(r.status)
    assert all(by_step[n] == {dc.PASS} for n in range(1, 7)), [(r.step, r.check, r.status, r.detail) for r in results if r.step <= 6]
    assert by_step[7] == {dc.TODO}
    assert "Next: DEPLOY.md step 7 (Create the member accounts and SCPs" in dc.report(results)


def test_root_without_mfa_with_access_keys_or_missing_contacts_fails_step_1():
    s = Stubs()
    secured_management(s, mfa=0, keys=1, missing_contact="SECURITY")
    r = {x.check: x for x in dc.step1(ctx(s))}
    assert r["root user has MFA"].status == dc.FAIL
    assert r["root user has no access keys"].status == dc.FAIL
    assert r["alternate contacts set"].status == dc.FAIL and r["alternate contacts set"].detail == "missing: security"


def test_before_the_organization_exists_later_steps_are_todo_not_failures():
    s = Stubs()
    s.err("mgmt", "organizations", "us-east-1", "describe_organization", "AWSOrganizationsNotInUseException")
    c = ctx(s)
    assert [r.status for r in dc.step3(c)] == [dc.TODO]
    assert [(r.status, r.detail) for r in dc.step6(c)] == [(dc.TODO, "needs step 3 first")]
    assert [(r.status, r.detail) for r in dc.step8(c)] == [(dc.TODO, "needs step 7 first")]


def test_step_7_finds_the_accounts_and_names_an_scp_attached_to_the_wrong_place():
    s = Stubs()
    s.ok("mgmt", "cloudformation", "us-east-1", "describe_stacks",
         {"Stacks": [{"StackName": "FrontDesk-Organization", "CreationTime": NOW, "StackStatus": "CREATE_COMPLETE"}]})
    accounts = [{"Id": i, "Arn": f"arn:aws:organizations::{MGMT}:account/o-abcdefghij/{i}", "Email": f"{n}@example.invalid",
                 "Name": n, "Status": "ACTIVE", "State": "ACTIVE"}
                for n, i in (("frontdesk-security", SEC), ("frontdesk-log-archive", LOGS), ("frontdesk-dev", DEV), ("frontdesk-prod", PROD))]
    s.ok("mgmt", "organizations", "us-east-1", "list_accounts", {"Accounts": accounts})
    names = sorted({f"frontdesk-{n}" for ns in dc.SCP_ATTACHMENTS.values() for n in ns})
    s.ok("mgmt", "organizations", "us-east-1", "list_policies", {"Policies": [
        {"Id": f"p-{i:08d}", "Arn": f"arn:aws:organizations::{MGMT}:policy/o-abcdefghij/service_control_policy/p-{i:08d}",
         "Name": n, "Type": "SERVICE_CONTROL_POLICY", "AwsManaged": False} for i, n in enumerate(names)]})
    where = {"root": ("ROOT", "Root", "r-abcd"), "security": ("ORGANIZATIONAL_UNIT", "Security", "ou-abcd-11111111"),
             "workloads": ("ORGANIZATIONAL_UNIT", "Workloads", "ou-abcd-22222222")}
    for n in names:
        targets = [t for t, ns in dc.SCP_ATTACHMENTS.items() if n.removeprefix("frontdesk-") in ns]
        if n == "frontdesk-protect-log-archive":
            targets = ["workloads"]  # the mistake the check must name
        s.ok("mgmt", "organizations", "us-east-1", "list_targets_for_policy", {"Targets": [
            {"TargetId": where[t][2], "Arn": f"arn:aws:organizations::{MGMT}:ou/o-abcdefghij/{where[t][2]}", "Name": where[t][1], "Type": where[t][0]}
            for t in targets]})
    c = ctx(s)
    r = {x.check: x for x in dc.step7(c)}
    assert c.facts["accounts"] == {"security": SEC, "logs": LOGS, "dev": DEV, "prod": PROD}
    assert all(r[f"account {n} active"].status == dc.PASS for n in dc.ACCOUNTS)
    bad = r["SCP frontdesk-protect-log-archive attached"]
    assert bad.status == dc.FAIL and "expected ['Security']" in bad.detail
    assert r["SCP frontdesk-deny-root-and-leaving attached"].status == dc.PASS


def test_a_profile_that_points_at_the_wrong_account_is_caught():
    s = Stubs()
    s.ok("dev", "sts", "af-south-1", "get_caller_identity", {"UserId": "AROAEXAMPLE:me", "Account": PROD, "Arn": f"arn:aws:sts::{PROD}:assumed-role/x/me"})
    c = ctx(s, dev="fd-dev")
    c.facts["accounts"] = {"dev": DEV}
    r = {x.check: x for x in dc.step10(c)}
    assert r["profile for the dev account"].status == dc.FAIL and DEV in r["profile for the dev account"].detail
    assert r["profile for the security account"].status == dc.SKIP


def test_an_unconfirmed_alert_e_mail_is_reported_because_alerts_would_go_nowhere():
    s = Stubs()
    s.ok("mgmt", "cloudtrail", "us-east-1", "describe_trails", {"trailList": [{"Name": "frontdesk-org-trail", "IsOrganizationTrail": True,
         "IsMultiRegionTrail": True, "LogFileValidationEnabled": True}]})
    s.ok("mgmt", "cloudtrail", "us-east-1", "get_trail_status", {"IsLogging": True})
    topic = f"arn:aws:sns:us-east-1:{MGMT}:frontdesk-security-alerts"
    s.ok("mgmt", "sns", "us-east-1", "list_topics", {"Topics": [{"TopicArn": topic}]})
    s.ok("mgmt", "sns", "us-east-1", "list_subscriptions_by_topic", {"Subscriptions": [
        {"SubscriptionArn": "PendingConfirmation", "Owner": MGMT, "Protocol": "email", "Endpoint": "me@example.invalid", "TopicArn": topic}]})
    r = {x.check: x for x in dc.step12(ctx(s))}
    assert r["organization trail logging"].status == dc.PASS
    assert r["security alert e-mail confirmed"].status == dc.FAIL and "Confirm subscription" in r["security alert e-mail confirmed"].detail
    assert r["FrontDesk-LogArchive stack deployed"].status == dc.SKIP


@pytest.mark.parametrize("status, expected", [("CREATE_COMPLETE", dc.PASS), ("UPDATE_COMPLETE", dc.PASS), ("CREATE_IN_PROGRESS", dc.TODO),
                                              ("UPDATE_ROLLBACK_COMPLETE", dc.FAIL), ("ROLLBACK_COMPLETE", dc.FAIL)])
def test_a_stack_counts_as_deployed_only_when_its_last_change_completed(status, expected):
    s = Stubs()
    s.ok("dev", "cloudformation", "af-south-1", "describe_stacks", {"Stacks": [{"StackName": "x", "CreationTime": NOW, "StackStatus": status}]})
    assert dc._stack(s("dev", "cloudformation", "af-south-1"), "x")[0] == expected


def test_an_expired_sso_sign_in_stops_with_the_command_to_sign_in_again():
    def expired(who, service, region):
        class C:
            def get_account_summary(self):
                raise UnauthorizedSSOTokenError()
        return C()

    with pytest.raises(dc.SignInExpired, match="aws sso login --profile fd-mgmt"):
        dc.run(dc.Ctx({"mgmt": "fd-mgmt"}, client=expired, gh=lambda a: None))


class _Inbox(http.server.BaseHTTPRequestHandler):
    headers_to_send: dict = {}

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        for k, v in self.headers_to_send.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b"<html></html>")

    def log_message(self, *a):
        pass


@pytest.mark.parametrize("headers, expected", [
    ({"content-security-policy": "default-src 'self'; script-src 'self'", "strict-transport-security": "max-age=63072000"}, dc.PASS),
    ({"content-security-policy": "default-src *"}, dc.FAIL),
])
def test_the_inbox_check_requires_the_csp_and_hsts_headers(headers, expected):
    _Inbox.headers_to_send = headers
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Inbox)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert dc._inbox_headers(f"http://127.0.0.1:{srv.server_address[1]}/inbox/").status == expected
    finally:
        srv.shutdown()


def test_the_checker_uses_the_same_names_as_the_cdk_app():
    scp_ts = (ROOT / "infra" / "lib" / "org" / "scp.ts").read_text()
    block = re.search(r"ATTACHMENTS[^=]*=\s*\{(.*?)\};", scp_ts, flags=re.S).group(1)
    ts = {k: re.findall(r"'([a-z-]+)'", v) for k, v in re.findall(r"(\w+):\s*\[([^\]]*)\]", block)}
    assert ts == dc.SCP_ATTACHMENTS
    org_ts = (ROOT / "infra" / "lib" / "org" / "organization-stack.ts").read_text()
    assert set(re.findall(r"account\('\w+', '([a-z-]+)'", org_ts)) == set(dc.ACCOUNTS)
    app_ts = (ROOT / "infra" / "lib" / "build-app.ts").read_text()
    assert {f"FrontDesk-dev-{n}" for n in re.findall(r"name\('(\w+)'\)", app_ts)} - {"FrontDesk-dev-GithubOidc"} == set(dc.DEV_STACKS)


def test_every_step_the_checker_reports_exists_in_deploy_md():
    titles = dc.step_titles()
    assert {n for n, _, _ in dc.STEPS} <= set(titles)


def test_the_cli_needs_the_management_profile_and_documented_commands_parse():
    with pytest.raises(SystemExit):
        dc.main([])
    text = (ROOT / "docs" / "DEPLOY.md").read_text()
    lines = re.findall(r"python -m frontdesk\.deploy_check ([^\n`]+)", text)
    assert lines
    for line in lines:
        dc.build_parser().parse_args(line.split())
