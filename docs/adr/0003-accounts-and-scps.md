# ADR 0003: Multi-account AWS Organizations with preventive SCPs

- Status: accepted
- Date: 2026-10-22

## Context
One account with an admin user is the most common way a small SaaS loses everything: one leaked
key reaches production data, logs and billing at once. Organizations, SCPs and IAM Identity
Center are free.

## Decision
Accounts: management (billing, Organizations, Identity Center, Budgets, org trail), security
(delegated admin for Access Analyzer and detections), log-archive (CloudTrail bucket with
Object Lock), dev, prod. OUs: Security, Workloads/Dev, Workloads/Prod.

SCPs (in `infra/policies/scp/`, tested in `infra/test/scp.test.ts`):
1. `deny-unapproved-regions`: only af-south-1, eu-central-1, and us-east-1 for global services.
2. `deny-bedrock-outside-frankfurt`: Bedrock only in eu-central-1 (ADR 0002).
3. `protect-audit-trail`: no StopLogging/DeleteTrail/UpdateTrail, no disabling Access Analyzer,
   no deleting log groups named `/frontdesk/audit/*`.
4. `deny-long-lived-credentials`: no IAM users, no access keys, no login profiles.
5. `deny-root-and-leaving`: member-account root denied everything; no LeaveOrganization.
6. `protect-log-archive` (log-archive OU only): no s3:DeleteObject*, PutBucketPolicy,
   PutObjectLockConfiguration, PutLifecycleConfiguration except by the break-glass role.

## Consequences
Five accounts to understand instead of one. Every new account must enable af-south-1 (opt-in).
Break-glass: the management account root, MFA on a hardware key, used only by runbook.
