# Detections and runbooks

Every alert Front Desk can raise, what it means, and what to do. A test
(`infra/test/stacks.test.ts`) fails the build if a detection in code has no section here.

Account-level detections (DET-01 to DET-04) run in the **management account, us-east-1**, on
the organization trail's CloudWatch log group, so they cover every account.
Application detections (DET-05 to DET-11) run in each workload account in af-south-1.
DET-10 runs in the security account.

Alerts arrive by e-mail (SNS). Severity: **P1** act within 15 minutes, **P2** same day, **P3** next working day.

General first steps for any P1: open `docs/INCIDENT-PLAN.md`, start the incident log (time,
who, what you saw), do not delete anything, and preserve CloudTrail by noting the event ids.

---

## DET-01

**Root user activity in any account. P1.**

Why it matters: root bypasses IAM and (in the management account) SCPs. We never use root
except by the break-glass runbook, so any activity is either that runbook or a compromise.

1. Check whether a break-glass session was announced in the incident log. If yes, record it and close.
2. If not: sign in to the management account root with the hardware MFA key, change the password, check MFA devices (remove any you do not own).
3. In CloudTrail (org trail, Athena or console), list all events with `userIdentity.type = Root` in the last 24 hours. Note source IPs.
4. Check for new IAM roles, users or access keys, changed SCPs, and disabled trails (DET-02, DET-03).
5. Contact AWS Support (account compromise) if anything unexplained remains.

Self-check: what is the one legitimate reason root is used in this organization?

## DET-02

**IAM change outside the deploy pipeline. P1.**

The filter ignores changes made by the CDK CloudFormation execution role. Anything else that
creates roles, attaches policies or removes a permission boundary is unexpected.

1. Read the event: who (`userIdentity.arn`), what (`eventName`, `requestParameters`), from where (`sourceIPAddress`).
2. If it was you in the console during an incident: record it, then put the change into CDK so the next deploy owns it.
3. Otherwise: revert the change (detach the policy, delete the role), revoke the principal's sessions (`aws iam put-role-policy` with an `aws:TokenIssueTime` deny, or delete the Identity Center session), and treat as DET-01 step 3 onward.

## DET-03

**CloudTrail tampering attempt. P1.**

SCPs deny these calls in member accounts, so a DET-03 from a member account means someone
tried and was denied; from the management account it may have succeeded.

1. Check the trail status: `aws cloudtrail get-trail-status --name frontdesk-org-trail --region us-east-1`. `IsLogging` must be true.
2. If logging stopped: `aws cloudtrail start-logging --name frontdesk-org-trail --region us-east-1`, then record the gap (start and end times) in the incident log.
3. Investigate the principal as in DET-02.

## DET-04

**Console sign-in without MFA. P1.**

Identity Center enforces MFA, so this should only be possible for a misconfigured or
compromised principal.

1. Identify the user from the event. Disable the user in Identity Center.
2. Check Identity Center MFA settings: "Every time they sign in", authenticator apps or security keys only.
3. Review what the session did (CloudTrail by `userIdentity.principalId`).

## DET-05

**One staff member read an unusual number of conversations in an hour. P2.**

Raised by the inbox API (`frontdesk/inbox_api.py`) when the audit log shows more transcript
reads by one staff member in an hour than `FRONTDESK_READ_ALERT_THRESHOLD` (default 40). It
is how a stolen staff session, or an insider exporting customer data, shows up.

1. In the inbox admin view (or `audit.who_read` per conversation), list what was read.
2. Call the staff member. If they did not do it: disable their Cognito user (`aws cognito-idp admin-disable-user`) and sign them out globally (`admin-user-global-sign-out`).
3. If customer data was likely exposed, start the POPIA s22 notification assessment in `docs/INCIDENT-PLAN.md` section 4.

## DET-06

**Degraded mode: the language layer is failing. P2.**

Customers are being told "a person will reply" and conversations are being handed to staff.
The business is still running (refusal 7 in AGENTS.md); staff load goes up.

1. Tell the client's staff that more conversations will land in the inbox.
2. Check the AWS Health Dashboard for Bedrock in eu-central-1, and the answer Lambda errors.
3. If a deploy is in progress, the canary will already have rolled back. If not, roll back manually: CodeDeploy, redeploy the previous version.
4. If it is a Bedrock quota (ThrottlingException), request a quota increase; the router can be pinned to the small model meanwhile (`FRONTDESK_FORCE_ROUTE=small`).

## DET-07

**Guardrail interventions spiking. P3, or P2 if combined with DET-09.**

Usually one person trying many prompt-injection variants. The guardrail is doing its job;
the alert exists so a sustained campaign is seen.

1. Look at the conversations with `refused` events in the last hour (inbox, filter "refused").
2. If one number or session: block it with the per-number rate limit (`scripts/block-sender.ps1 --dry-run`, then `--write`).
3. Add any novel attack that got further than expected to `evals/redteam/cases.yaml` so it is tested on every build.

## DET-08

**Signature verification failed on a webhook or SNS message. P2.**

Someone is sending forged events to our endpoints, or a client integration is misconfigured.

1. Check the source IP and the endpoint in the log line (`event=signature_failed`).
2. If it is a client integration after a secret rotation: confirm the client has the new secret.
3. Otherwise: no action is needed for the rejected message itself; watch for volume (DET-09).

## DET-09

**Rate limits hit unusually often. P3.**

Abuse of the public bot (spam, cost attack) or a stuck client integration in a loop.

1. Find the top keys in `app.rate_limits` for the last hour.
2. Block abusive senders as in DET-07 step 2. Check the Bedrock spend metric is still within budget.

## DET-10

**IAM Access Analyzer found a resource shared outside the organization. P2.**

1. Open the finding in the security account (Access Analyzer, Region af-south-1).
2. If intended (for example a client's bucket in an integration): archive the finding with the reason.
3. If not: remove the external principal from the resource policy, then find out how it got there (CloudTrail).

## DET-11

**A system-prompt canary token appeared in model output. P2.**

The answer prompt contains a random canary string. If it ever appears in output, a customer
extracted the system prompt. The validator blocks that reply before it is sent; this alert
says it happened.

1. Read the conversation. Add the extraction technique to `evals/redteam/cases.yaml`.
2. Rotate the canary (`FRONTDESK_PROMPT_CANARY` in SSM) and review the prompt for anything that should not be there (it must never contain secrets).

## DET-12

**What customers ask about shifted this week (intent-mix PSI 0.25 or more). P3.**

Not an attack: a quality warning. The documents, glossary and evaluation set were built for
last month's questions; when the mix moves, answers can get worse without any code change.
The weekly job (`handlers/ops.drift`, Mondays 07:00 SAST) compares each tenant's last seven
days with the four weeks before (`frontdesk/drift.py`). It alarms only when the shift is large
(PSI 0.25 or more after merging intents under 5%) *and* unlikely to be noise (chi-square
p < 0.01); weeks with fewer than 30 messages are "too few to judge". Measured false-alarm rate
about 2% of weeks (`docs/evidence/insights-demo.md` section 4).

1. Open the drift Lambda's latest invocation (`/frontdesk/<env>/drift` log group): it lists
   `psi`, `level` and message counts per tenant, with no text. The insights screen shows the
   tenant's top intents for the same week.
2. Read the moved intents against the calendar: load-shedding stages, a price change, a new
   product, a marketing campaign. A known cause with answers in the documents needs no action.
3. If the moved intent is mostly handed over as "unanswerable", that is an FAQ gap: add it to
   the client's next document update (owner report "could not answer" list).
4. Add ten real, redacted examples of the new questions to the client's evaluation set and
   rerun the evals before the next release.

