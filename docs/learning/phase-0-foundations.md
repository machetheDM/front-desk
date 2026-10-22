# Phase 0: foundations, in plain language

What you ran, why it works, and what to say about it. Each section ends with a line for an
interview and a question to check yourself.

## 1. Why five AWS accounts instead of one

An AWS account is the strongest wall AWS offers: nothing in one account can touch another
unless someone deliberately allows it. We use five: **management** (billing and the rules),
**security** (watches everything), **log-archive** (keeps the CloudTrail logs where nobody
can delete them), **dev** and **prod**. If a developer key for dev leaks, prod data, the logs
and the billing are in other accounts.

Service control policies (SCPs) are rules the management account puts on the others. They do
not give permissions; they set a ceiling that even an administrator inside the account cannot
go above. Ours say: only Cape Town and Frankfurt; Bedrock only in the EU and never "Global";
no IAM users or access keys; nobody may stop CloudTrail or delete the archived logs.

We test the SCPs like code: `infra/test/scp.test.ts` contains a small evaluator and asks
questions such as "would starting a database in Frankfurt be denied?" If someone edits an SCP
and opens a hole, a test goes red.

> Interview line: "I split the platform into management, security, log-archive, dev and prod
> accounts and put preventive SCPs on them, which I unit-test with a small policy evaluator, so
> a change that opens a Region or allows IAM users fails CI."

Check yourself: an SCP allows nothing. So what actually grants a Lambda permission to call Bedrock?

## 2. Why Bedrock runs in Frankfurt and everything else in Cape Town

We checked the AWS Price List API (it lists every price AWS charges, per Region) for Cape
Town. Claude is sold there only as **Global** inference: AWS may process the request in any
Region in the world. There are no embedding, rerank or Guardrails models at all. Frankfurt has
all of them. POPIA section 72 allows sending personal information abroad if the other country
has adequate protection; Germany (GDPR) clearly does, "anywhere in the world" is impossible to
assess. So data lives in Cape Town, and only *redacted* text goes to Frankfurt for the
language work. The decision is ADR 0002, and an SCP makes the Global option impossible.

> Interview line: "The region decision came from the Price List API, not from memory: Cape
> Town had Claude only via Global routing, so I kept data at rest in af-south-1, sent only
> redacted text to Bedrock in Frankfurt, wrote the POPIA s72 assessment, and enforced it with
> an SCP that denies Global inference profiles."

Check yourself: which two things would make you move the model calls back to Cape Town?

## 3. Tenant isolation below the application

Every business table has a `tenant_id` and **row-level security** (RLS): Postgres itself adds
"and tenant_id = the current tenant" to every query. The application sets the current tenant
at the start of each transaction (`set_config('app.tenant_id', ..., true)`; `true` means "for
this transaction only"). Three details make it real rather than decorative:

- `FORCE ROW LEVEL SECURITY`: without it, the table owner skips RLS.
- The app logs in as `frontdesk_app`, which owns nothing and cannot bypass RLS.
- If no tenant is set, the setting is empty, the policy compares with NULL, and **nothing**
  matches. Forgetting to set it fails closed.

The embeddings live in the same rows as the text, so vector search is filtered by the same
rule. `backend/tests/test_tenant_isolation.py` runs against a real Postgres with pgvector and
proves a conversation, a document, an exact-match vector search, a full-text search and the
retrieval module never cross tenants. The migration linter and a catalog check make "a new
table without RLS" a failing migration.

> Interview line: "Isolation is enforced by Postgres, not by remembering WHERE clauses: forced
> RLS, a non-owner app role, fail-closed tenant context, and the embeddings in the same
> RLS-protected rows; a two-tenant test suite and a migration linter fail the build if any of
> it regresses."

Check yourself: why would the isolation tests pass for the wrong reason if they connected as `postgres`?

## 4. The database has no door to the internet

Aurora sits in subnets with no route out and no route in. Lambdas do not join that network;
they use the **RDS Data API**, an HTTPS API where AWS checks IAM permissions on every call.
That saves a NAT gateway (about USD 45 a month) and means "who can query the database" is an
IAM question that CloudTrail records.

> Interview line: "The database is unreachable from any network; the only path is the
> IAM-authenticated RDS Data API, which also removed the need for a NAT gateway and kept us
> under budget."

Check yourself: what does the Data API need besides IAM permission, and why does that force Aurora credentials into Secrets Manager rather than SSM?

## 5. CI that fails for the right reasons

Every pull request runs: backend tests (with a real pgvector database), the quality gates,
TypeScript tests, `cdk synth` with cdk-nag, cfn-guard on every template, gitleaks, Semgrep,
Trivy and an SBOM. A scanner that is misconfigured passes everything silently, so
`security/gate-selftest.sh` feeds each gate a known-bad example and fails if the gate does not
fail. GitHub reaches AWS with OIDC: a short-lived token, trusted only for this repository and
a named GitHub environment. There is no AWS key anywhere to leak.

> Interview line: "Every security gate has a kept failing example that CI runs, so I can show
> the gate actually blocks; deploys use OIDC pinned to the repo and environment, so there are
> no long-lived AWS keys."

Check yourself: why is the prod deploy role trusted for `environment:prod` rather than `ref:refs/heads/main`?

## 6. Money

`tools/pricing/fetch_prices.py` downloads the exact prices from the AWS Price List API and
`cost_model.py` computes the monthly cost for idle, demo and pilot. Idle is about R84, demo
about R270, pilot about R1,089 (passed through to the client). Budgets e-mail you at 50%, 80%
and 100% of R300, and when the forecast passes R300. Claude on Bedrock is billed through AWS
Marketplace, which a "Service = Bedrock" budget filter misses, so the app caps tokens itself.

> Interview line: "Every rand in the cost table traces to an AWS Price List SKU via a script,
> and I found that a service-filtered Bedrock budget would miss Claude's Marketplace billing,
> so spend is capped in the application."

Check yourself: what single assumption moves the pilot cost the most, and how would you reduce it?
