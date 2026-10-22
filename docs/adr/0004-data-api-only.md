# ADR 0004: Aurora is reached only through the RDS Data API

- Status: accepted
- Date: 2026-10-22

## Context
Lambdas inside a VPC need a NAT gateway (about USD 45/month per AZ) or interface endpoints
(about USD 8/month each per AZ) to reach Bedrock, SSM and S3 APIs. Both break the R300 cap.
A database with a public endpoint is not acceptable.

## Decision
Aurora sits in isolated subnets with no route out. `enableDataApi` is on. Lambdas run outside
the VPC and call `rds-data:ExecuteStatement` with IAM, scoped to the cluster ARN and the one
Secrets Manager secret. RLS context is set with `set_config('app.tenant_id', ..., true)` inside a
Data API transaction (`BeginTransaction` ... `CommitTransaction`), so the same isolation rules
hold as with a direct connection.

**The Secrets Manager secrets:** the Data API authenticates to Postgres with a Secrets Manager
secret ARN; it cannot read SSM. So the two Aurora credentials (the RDS-generated admin, used only
by the migrate Lambda, and the non-owner `frontdesk_app`) are in Secrets Manager. Everything else
stays in SSM SecureString.

## Consequences
- No NAT, no public DB, IAM decides who reaches data, every call is in CloudTrail (data events
  optional, paid).
- Data API limits: 1 MB response, 45 s statement timeout, no `LISTEN`. Retrieval returns IDs and
  small text, which fits.
- If scale-to-zero is unavailable in af-south-1, dev pauses the cluster between sessions
  (`scripts/db-pause.ps1`).
