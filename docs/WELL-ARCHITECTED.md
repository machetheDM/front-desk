# Well-Architected review

Review of Front Desk against the six pillars of the AWS Well-Architected Framework and the
Generative AI Lens, at the end of Phase 5 (23 October 2026), for the pilot architecture in
`docs/ARCHITECTURE.md`. Organised by each pillar's question topics rather than question
numbers, which change between framework versions; when doing a formal review with a client,
load the workload into the Well-Architected Tool and carry these answers across.

Every risk is **closed** (control built and evidenced), **accepted** (written down with the
reason and when to revisit, and in the threat model's accepted-risk table where it is a
security risk) or **open** (to do, with an owner). HRI and MRI mean high- and medium-risk issue.

## Summary

| Pillar | HRIs | MRIs | Open | Headline |
|---|---|---|---|---|
| Operational excellence | 0 | 0 | 0 | Everything is code, every gate has a failing example, drills run in CI, one operations dashboard |
| Security | 0 | 4 accepted | 0 | Isolation below the app, no long-lived keys, detections with runbooks. Paid services deferred to the first client |
| Reliability | 0 | 2 (1 accepted) | 2 | Model outage and restore drilled locally; AWS restore timing and quota check still to do |
| Performance efficiency | 0 | 1 | 1 | Measured offline; Bedrock latency measured only once the dev account exists |
| Cost optimisation | 0 | 0 | 0 | R84 idle, R270 demo, every service in the cost table, a token cap because Marketplace billing escapes Budgets filters |
| Sustainability | 0 | 0 | 0 | Scale to zero, Graviton, the small model first |
| Generative AI Lens | 0 | 1 | 1 | Grounding, guardrails, evaluation gates and a red-team suite in CI; the Bedrock numbers are seams |

No high-risk issue is open. The accepted MRIs are each tied to "first paying client".

## Operational excellence

| Question topic | Answer | Status | Evidence |
|---|---|---|---|
| Priorities and organisation | The refusals and rules in `AGENTS.md`; the depth checklist decides "done" | closed | `AGENTS.md`, `docs/DEPTH.md` |
| Design for operations | Infrastructure, policies, detections and runbooks are all code; one migration per change; PowerShell for the owner, parsed in CI | closed | `infra/`, `db/migrations/`, `scripts/ci/check-powershell.ps1` |
| Safe change | CI gates, signed Lambda code, canary with automatic rollback on alarms, preview before every write | closed | `.github/workflows/ci.yml`, `stacks.test.ts`, `write_guard.py` |
| Readiness and drills | Model-down and backup/restore drills run on every pull request; tabletop for a stolen session | closed | `docs/drills/`, `test_drills.py`, `test_restore.py` |
| Understanding the workload | Structured JSON logs, EMF metrics, X-Ray tracing on every Lambda; one operations dashboard (latency and cost by route, degraded and paused, attacks, drift, alarms); conversation events drive the owner's insights | closed | `app-stack.ts` `Operations` dashboard, `stacks.test.ts` "operations dashboard" |
| Learning from operations | DORA metrics weekly; blameless write-ups with a new test or detection per incident | closed | `.github/workflows/dora.yml`, `docs/INCIDENT-PLAN.md` section 5 |

## Security

| Question topic | Answer | Status | Evidence |
|---|---|---|---|
| Securely operate (accounts, guardrails) | Organizations with five accounts, six SCPs tested by an evaluator | closed | `infra/policies/scp/`, `scp.test.ts` |
| Identities for people and machines | Identity Center with MFA; GitHub OIDC pinned to repo and environment; no IAM users or access keys (SCP) | closed | `github-oidc-stack.ts`, SCP `deny-long-lived-credentials` |
| Permissions | Permission boundary on every workload role; least-privilege grants; app DB role owns nothing | closed | `stacks.test.ts` boundary test, `test_tenant_isolation.py` |
| Detection | Org CloudTrail with Object Lock; DET-01 to DET-12 with runbooks, firing in tests | closed | `docs/DETECTIONS.md`, `detections.test.ts` |
| Network | Database in isolated subnets, no internet gateway, no NAT, Data API only | closed | `data-stack.ts`, ADR 0004 |
| Compute | Lambda only, signed code enforced in prod, pinned dependencies scanned | closed | `stacks.test.ts`, CI `security` job |
| Data classification and protection | PII redacted before any model; KMS at rest; TLS in transit; tenant isolation by forced RLS | closed | `redact.py`, `test_redaction.py`, `docs/POPIA-S72.md` |
| Incident response | Incident plan, kill switch per tenant, immediate staff cut-off, breach notice template | closed | `docs/INCIDENT-PLAN.md`, `test_controls.py`, `test_incident_tools.py` |
| Application security | ASVS L2 mapping, Semgrep with custom rules, ZAP workflow, red-team suite as a gate | closed | `docs/ASVS.md`, `docs/AI-SECURITY.md` |
| Phishing-resistant MFA for staff | TOTP only on Cognito Lite | MRI accepted (R-03) | Revisit at first paying client; scenario in `docs/drills/2026-10-23-tabletop-stolen-session.md` |
| Managed threat detection | GuardDuty, Security Hub, Config off for cost | MRI accepted (R-02) | CloudTrail detections compensate; first paying client |
| Edge protection | No WAF | MRI accepted (R-01) | Stage throttling and per-sender limits; first paying client |
| TLS policy at the viewer | Default CloudFront certificate allows TLS 1.0 | MRI accepted (R-04) | Custom domain with the first client |

## Reliability

| Question topic | Answer | Status | Evidence |
|---|---|---|---|
| Service quotas | Bedrock on-demand quotas in eu-central-1 not yet checked for the pilot's peak (about 3 messages a minute) | open | Check in Service Quotas after DEPLOY.md step 14; owner Machethe DM |
| Workload architecture | Step Functions Express with a Catch on every answer; ids-only payloads; idempotent intake by message id | closed | `stacks.test.ts` Catch test, `test_pipeline.py` duplicate test |
| Withstand component failure | Model down means "a person will reply" and the inbox keeps working | closed | `docs/drills/2026-10-23-model-down.md` |
| Adapt to demand | Serverless throughout; Aurora 0 to 2 ACU (4 in prod); the first call after a pause waits for the cluster to wake instead of failing | closed | `data-stack.ts`, `db/dataapi.call_resuming`, ADR 0004 addendum |
| Back up and restore | Continuous backup (7 days dev, 14 prod); restore check proves protections survive | MRI open | Local drill done; the AWS point-in-time restore with measured RTO is SEAM(restore-drill-aws) |
| Fault isolation | One Region for data; Aurora storage is replicated across three AZs, but there is a single Serverless v2 writer and no reader, so a writer failure means minutes to replace it | MRI accepted | Within the 2-hour RTO target for a pilot; add a reader (doubles the database floor cost) when a client's SLA needs it |
| Test reliability | Drills in CI; canary deploys | closed | `test_drills.py`, `test_restore.py` |

## Performance efficiency

| Question topic | Answer | Status | Evidence |
|---|---|---|---|
| Selection | Lambda arm64; Aurora Serverless v2 with pgvector (one store for vectors, text and RLS); Bedrock in the nearest Region with the needed models | closed | ADR 0002, ADR 0004 |
| Measure | Offline latency per route in every eval report; prompt caching for the system prompt and tools | closed offline | `docs/evidence/eval-offline-*.md`, `llm/bedrock.py` |
| Real latency | Bedrock latency from Cape Town to Frankfurt per answer, and WebSocket time to first progress event | MRI open | Needs the dev account; release evals record it per route |

## Cost optimisation

| Question topic | Answer | Status | Evidence |
|---|---|---|---|
| Financial management | A row for every service with its free tier; R300 a month ceiling; Budgets alarms at 50%, 80% and 100% actual and 100% forecast | closed | `docs/ARCHITECTURE.md` section 4, `budgets-stack.ts` |
| Usage awareness | Per-tenant token metering and a monthly cap, because Claude on Bedrock bills through Marketplace and escapes service filters in Budgets | closed | `llm/metering.py`, `test_metering_and_router.py` |
| Cost-effective resources | Scale to zero (Aurora 0 ACU, Lambda), small model first with routing, 1 GB scan cap per Athena query | closed | `llm/router.py`, `app-stack.ts` workgroup |
| Expenditure over time | Paid security services listed with prices and switched on only with an ADR | closed | ARCHITECTURE "When a client pays" row |

## Sustainability

| Question topic | Answer | Status | Evidence |
|---|---|---|---|
| Region selection | Data and compute in Cape Town, near the users; only redacted text crosses to Frankfurt | closed | ADR 0002 |
| Demand alignment | Nothing runs idle: Aurora pauses, Lambdas scale to zero, nightly and weekly jobs only | closed | `data-stack.ts`, `app-stack.ts` schedules |
| Hardware and software | Graviton (arm64) Lambdas; the small model answers unless the question needs the large one | closed | `app-stack.ts`, `llm/router.py` |

## Generative AI Lens

| Focus area | Answer | Status | Evidence |
|---|---|---|---|
| Model selection | Haiku first, Sonnet when routed; LLM classifier arm built for comparison | closed in code | `llm/router.py`, `ml/frontdesk_ml/llm_classifier.py` |
| Grounding (RAG) | Hybrid retrieval with rerank; no source, no answer; numbers must appear in the cited text | closed | `llm/validate.py`, eval report |
| Guardrails and responsible AI | Bedrock Guardrails in and out; the refusals enforced in code; model card | closed in code; live run is a seam | `ai-stack.ts`, `docs/MODEL-CARD.md` |
| Evaluation | Floors in `docs/PLAN.md` gate every pull request offline and every release on Bedrock | MRI open | Bedrock floors measured once the dev account exists (release-evals workflow) |
| Prompt management | Versioned prompts with hashes on every answer; pre-registered A/B tests with a refusal to peek | closed | `llm/prompts/`, `experiments/`, `ml/frontdesk_ml/ab.py` |
| Security of the AI | OWASP LLM Top 10 and MITRE ATLAS mapping, 42-case red-team suite in CI | closed | `docs/AI-SECURITY.md` |
| Data governance | Documents approved, hashed and versioned; poisoned text quarantined; no training across tenants | closed | `retrieval/ingest.py`, `retrieval/quarantine.py`, `AGENTS.md` refusal 6 |
| Monitoring in production | Drift on the intent mix weekly (DET-12); shadow classifier logged; guardrail and canary detections | closed | `drift.py`, `test_drift.py`, `docs/DETECTIONS.md` |
