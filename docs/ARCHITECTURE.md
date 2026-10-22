# Front Desk: architecture

## 1. Shape of the system (pilot scale)

```
 Customer (WhatsApp)                         Customer (website)
        │                                           │
 AWS End User Messaging Social (af-south-1)   CloudFront ─► chat widget (S3, OAC)
        │ SNS event (signature verified)            │ HTTPS, per-tenant widget key
        └──────────────┬────────────────────────────┘
                       ▼
        API Gateway HTTP API / SNS ─► Lambda: conversation intake
           rate limit per number and tenant ─► consent check ─► event "message_received"
                       ▼
        Step Functions Express: screen ─► answer ─► deliver
           screen : PII redaction, injection heuristics, opt-out/STOP keywords
           answer : agent state machine (typed tools, model routing, prompt cache)
                    └─► Bedrock eu-central-1 (Claude Haiku/Sonnet, Guardrails,
                        Cohere Embed Multilingual, Cohere Rerank 3.5) ── redacted text only
           deliver: schema + citation + grounding checks ─► reply, or hand over
           Catch  : any Bedrock failure ─► DEGRADED: "a person will reply", handoff
                       │
   ┌───────────────────┼──────────────────────────────┐
   ▼                   ▼                              ▼
 Aurora PostgreSQL   S3 (source docs, transcripts,  Staff inbox: Next.js static export
 Serverless v2       analytics) SSE-KMS, per-tenant on S3 + CloudFront; Cognito MFA;
 RLS on tenant_id,   prefix, Block Public Access    API via HTTP API JWT authorizer
 pgvector + FTS,     │
 append-only audit;  └─► nightly export ─► S3 Iceberg ─► Athena ─► insights, clustering
 RDS Data API only, no network path from the internet, no NAT
```

Accounts (AWS Organizations, ADR 0003):

```
 Management (payer)  ── Organizations, SCPs, IAM Identity Center, Budgets, org CloudTrail
 ├── OU Security
 │    ├── security     ── delegated admin: IAM Access Analyzer, detections, (GuardDuty later)
 │    └── log-archive  ── org CloudTrail bucket, S3 Object Lock, no human write access
 └── OU Workloads
      ├── OU Dev  └── dev   ── everything in this doc, demo tenant only
      └── OU Prod └── prod  ── real tenants; signed Lambda code enforced
```

## 2. Region matrix (checked 22 October 2026)

Sources, in order of authority:

- **AWS Price List API** (`tools/pricing/fetch_prices.py`, snapshot in
  `docs/evidence/pricing-snapshot.json`). If AWS publishes a price for a usage type in a Region,
  the service is sold there. If the Region file has no such usage type, it is not.
- **AWS documentation**, read through search results because `docs.aws.amazon.com` is blocked by
  this build container's network policy. Each such row says so.
- **`scripts/check-regions.ps1`**, which the owner runs against the real account. Its output is
  committed to `docs/evidence/region-check.json` and overrides everything above. Rows marked
  *verify* are settled only by that run.

| Capability | af-south-1 (Cape Town) | Evidence | Decision |
|---|---|---|---|
| Bedrock: Claude Haiku 5.5, Sonnet 5.5 | **Global cross-Region inference only** (no in-Region, no geo) | Price List: af-south-1 Claude SKUs are all `*_global_*`; AWS model cards (via search) list Cape Town as Global only | Call from **eu-central-1** in-Region or EU geo. Global from af-south-1 would send customer text to any commercial Region worldwide; SCP denies `bedrock:*` in af-south-1 |
| Bedrock: embeddings (Titan V2, Cohere Embed) | **Not offered** | Price List: no embedding usage types in af-south-1 | eu-central-1: Cohere Embed Multilingual (Sepedi/English) primary, Titan V2 compared |
| Bedrock: rerank | **Not offered** (also not in eu-west-1) | Price List | eu-central-1: Cohere Rerank 3.5, Amazon Rerank 1.0 |
| Bedrock Guardrails | **Not offered** | Price List: no Guardrail usage types in af-south-1 | eu-central-1 |
| End User Messaging Social (WhatsApp) | **Available** | Price List: `AFS1-WhatsApp-ZA-*` fees; AWS General Reference endpoint `social-messaging.af-south-1.amazonaws.com` (via search) | af-south-1 |
| Aurora PostgreSQL Serverless v2 | **Available** | Price List: `AFS1-Aurora:ServerlessV2Usage` USD 0.16/ACU-hr | af-south-1 |
| Aurora scale to zero (0 ACU, auto-pause) | *verify* | Docs (via search): needs Aurora PostgreSQL 16.3+; no Region list found. `check-regions.ps1` reads `ServerlessV2FeaturesSupport.MinCapacity` from `describe-db-engine-versions` | af-south-1 if MinCapacity is 0; otherwise min 0.5 ACU and the idle cost row changes (see cost table) |
| RDS Data API (Aurora PostgreSQL) | **Available** | Price List: `AFS1-RDS:Data-API-Usage` "Aurora PostgreSQL Data API request" | af-south-1 |
| Amplify Hosting | **Not offered** | Price List: no AWSAmplify Region file for af-south-1; launch notes list no Cape Town | Static export on S3 + CloudFront (ADR 0005) |
| Cognito user pools | **Available** | Price List: `AFS1-CognitoLiteMAU` | af-south-1 |
| Lambda, API Gateway HTTP API, Step Functions Express, KMS, S3, CloudWatch, Athena, CloudTrail, Secrets Manager, EventBridge | **Available** | Price List (af-south-1 SKUs in snapshot) | af-south-1 |
| IAM Identity Center | **Available as an opt-in Region**; the Region must be enabled in every member account | AWS docs (via search): Cape Town is opt-in for Identity Center; multi-Region replication not supported for opt-in Regions | af-south-1 (all accounts enable af-south-1 anyway) |
| Budgets, Organizations, IAM, CloudFront, ACM for CloudFront | Global, homed in us-east-1 | Price List: AWSBudgets has no af-south-1 file (global) | us-east-1 allowed by SCP for these only |
| GuardDuty, Security Hub, Config, WAF | Available | Price List | Designed; **switched on when a client pays** |

**af-south-1 is an opt-in Region.** It must be enabled in each account before anything deploys
there (docs/DEPLOY.md step 3).

### Cross-border flow (POPIA s72)

Only one flow leaves South Africa with customer content: redacted question text, retrieved
company document chunks and the conversation summary, sent to Amazon Bedrock in Frankfurt.
SA ID numbers, card and bank numbers, phone numbers and emails are replaced with placeholders
**before** the call (`backend/frontdesk/redact.py`, tested). Bedrock does not store prompts or
use them for training. The written s72 assessment is `docs/POPIA-S72.md`.

## 3. Data model in one paragraph

Every business table has `tenant_id uuid not null`, row-level security enabled **and forced**,
and a policy `tenant_id = current_setting('app.tenant_id')::uuid`. The application connects as
`frontdesk_app`, which owns nothing and has no `BYPASSRLS`. Each request opens a transaction,
sets `app.tenant_id` with `set_config(..., true)` (transaction-local), and every query in that
transaction is filtered by Postgres itself. Document chunks carry their embedding
(`vector(1024)`) and a `tsvector` in the same row, so vector search, full-text search and
isolation are one mechanism. Conversations are an append-only event stream
(`conversation_events`); the dashboard is computed from events. `audit_log` is append-only by
trigger and records reads as well as writes.

## 4. Cost table

Numbers come from `docs/evidence/cost-estimate.md`, generated from the Price List snapshot.
Prices are af-south-1 unless the row says eu-central-1. "Free allowance" marked † is visible in
the Price List itself; ‡ is from AWS's free-tier page as of the July 2025 change (new accounts
get the Free plan with credits instead of the old 12-month tier) and must be re-checked at
<https://aws.amazon.com/free/> because this container cannot reach it.

| Service | What it does here | Free allowance | Price beyond (USD) | When the allowance runs out |
|---|---|---|---|---|
| Bedrock Claude Haiku 5.5 (eu-central-1) | Default answer model | None | 0.11 in / 0.55 out per 1M tokens; cache read 0.011 | Billed per token; budget alarm at 50/80/100% |
| Bedrock Claude Sonnet 5.5 (eu-central-1) | Escalation model when router says hard | None | 2.20 in / 11.00 out per 1M tokens | Router caps Sonnet share; per-tenant monthly token cap |
| Bedrock Cohere Embed Multilingual (eu-central-1) | Chunk and query embeddings | None | 0.10 per 1M tokens | Negligible at pilot scale |
| Bedrock Cohere Rerank 3.5 (eu-central-1) | Rerank top 20 hybrid results | None | 0.002 per search unit (≤100 docs) | Largest AI line after Claude at pilot scale; can fall back to lexical reranker |
| Bedrock Guardrails (eu-central-1) | Prompt-attack, content and grounding checks | Word filter and PII *free* tier † | 0.15 per 1K text units (content), 0.10 (grounding) | Billed per text unit |
| Aurora PostgreSQL Serverless v2 | System of record, pgvector, FTS, audit | None ‡ | 0.16 per ACU-hour; storage 0.131 per GB-month | Scale to zero keeps idle cost to storage; awake time is the swing |
| RDS Data API | Only path from Lambda to the database | None | 0.000000467 per request | Pennies |
| Lambda (ARM) | All compute | 1M requests, 400K GB-s per month ‡ | 0.27 per 1M requests; 0.0000177 per GB-s | Billed at listed rate |
| API Gateway HTTP API | Web chat and inbox API | 12-month offer for old accounts only ‡ | 1.32 per 1M requests | Billed at listed rate |
| Step Functions Express | Conversation pipeline with Catch to degraded mode | 4,000 *standard* transitions † (Express not covered) | 1.33 per 1M requests + duration | Billed at listed rate |
| S3 | Documents, transcripts, analytics, CloudTrail archive | Old 5 GB 12-month offer only ‡ | 0.0274 per GB-month | Billed at listed rate |
| KMS | One customer-managed key per environment, tenant in encryption context | 20,000 requests per month † | 1.00 per key-month; 0.03 per 10K requests | Each extra key is R18.50 a month, so keys are per environment, not per tenant |
| Secrets Manager | Aurora credentials (the Data API requires Secrets Manager): admin for migrations, app for Lambdas | 30-day trial per secret ‡ | 0.40 per secret-month | Exactly two per environment |
| SSM Parameter Store SecureString | All other secrets and config | Standard parameters free ‡ | Advanced parameters 0.05/month (not used) | n/a |
| Cognito (Lite) | Staff sign-in with MFA | 10,000 MAU † | 0.0055 per MAU | Staff only, so effectively free |
| CloudWatch | Logs, metrics, alarms, detections | 5 GB logs, 10 metrics, 10 alarms ‡ | 0.678 per GB ingested; 0.10 per alarm | 14-day log retention by default to cap it |
| EventBridge | Detection rules, schedules | AWS-service events free ‡ | Custom events 1.00 per 1M | n/a |
| CloudTrail | Org trail to log-archive | First copy of management events free † | 0.00002 per extra event | We keep one trail |
| Athena | Insights queries over Iceberg | None | 6.00 per TB scanned | Workgroup cap of 1 GB scanned per query |
| CloudFront | Inbox and widget hosting | Always-free 1 TB, 10M requests ‡ (flat-rate plans also exist) | Per GB beyond | n/a at pilot scale |
| Budgets | Spend alarms | First 2 action-enabled budgets free; alerts free ‡ | 0.02 per budget-day beyond | n/a |
| End User Messaging Social | WhatsApp number | None | AWS fee 0.005 per message (ZA) + Meta fee: service 0.00, utility 0.006, marketing 0.03 per message (ZA) | Passed through to client |
| Organizations, SCPs, IAM Identity Center, Access Analyzer (external access), Signer for Lambda, CodeDeploy for Lambda | Accounts, guardrails, SSO, signed code, canary | Free ‡ | Access Analyzer *unused-access* is paid (not enabled) | n/a |
| **When a client pays:** GuardDuty, Security Hub, Config, WAF, interface VPC endpoints, NAT | Designed in docs/THREAT-MODEL.md and docs/DETECTIONS.md | Trials only | GuardDuty 0.000005 per CloudTrail event; Security Hub 0.001 per check; Config 0.003 per item; WAF 5.00 per web ACL + 1.00 per rule | Not switched on quietly. Enabling one needs an ADR and a budget update |

**Monthly totals** (from `docs/evidence/cost-estimate.md`, R18.50/USD assumed):

| Scenario | USD | ZAR | Within R300? |
|---|---|---|---|
| idle (dev deployed, nobody using it) | 4.53 | R84 | yes |
| demo (600 answers, 300 WhatsApp replies) | 14.61 | R270 | yes, close: the 80% budget alarm (R240) will fire |
| pilot (1,500 answers, 2,000 WhatsApp messages, DB awake 200 h) | 58.87 | R1,089 | no, passed through to the client |

If `check-regions.ps1` shows af-south-1 does not support 0 ACU, the idle Aurora row becomes
0.5 ACU around the clock (USD 58/month). In that case dev runs the database only during work
sessions (`scripts/db-pause.ps1`), and the decision goes into ADR 0004.

## 5. Security architecture in brief

Detail is in `docs/THREAT-MODEL.md` and `docs/AI-SECURITY.md`.

- **Identity**: IAM Identity Center with MFA for humans; no IAM users (SCP-denied); GitHub
  Actions through OIDC roles scoped to this repository and branch; permission boundaries on
  every role the app creates.
- **Network**: Aurora in isolated subnets with no internet route and no NAT; Lambda reaches it
  through the Data API (IAM-authenticated, HTTPS). Lambdas run outside the VPC.
- **Data**: KMS CMK per environment with `tenant_id` in the encryption context for S3 objects;
  RLS in Postgres; per-tenant S3 prefixes with IAM conditions; deletion that reaches
  transcripts, chunks and embeddings.
- **Detection**: EventBridge rules for root sign-in, IAM changes, CloudTrail tampering, Guardrail
  intervention spikes and unusual transcript reads, each with a runbook in `docs/DETECTIONS.md`.
- **Supply chain**: Semgrep, gitleaks, Trivy, SBOM, cdk-nag, cfn-guard on every pull request;
  AWS Signer code signing enforced in prod; CodeDeploy canary with alarm rollback.
