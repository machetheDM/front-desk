# ADR 0007: CloudTrail archive uses S3 Object Lock in governance mode until a client pays

- Status: accepted
- Date: 2026-10-22

## Context
Compliance mode cannot be shortened or removed by anyone, including root, until the retention
period ends. A mistake (wrong retention, wrong bucket) during the build would be permanent and
billed. Governance mode blocks deletion unless a principal has
`s3:BypassGovernanceRetention`, which the log-archive SCP denies to everyone but break-glass.

## Decision
Governance mode, 365-day default retention, during Phase 0 to 5. Switch to compliance mode in
prod when the first client signs, recorded as a new ADR.

## Consequences
The protection is "denied by SCP" rather than "impossible". Documented as an accepted risk in
the threat model (R-07).
