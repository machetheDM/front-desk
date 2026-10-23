# Threat model

Scope: Front Desk as deployed per `docs/ARCHITECTURE.md`. Method: STRIDE per trust boundary,
then AI-specific threats, then attack trees for the three outcomes that would end the business.
Each threat names its control and the evidence (test, rule, or detection) that the control works.
Living document: reviewed at the end of each phase; last review Phase 4 (23 October 2026).

## 1. Assets

| Asset | Why it matters |
|---|---|
| Customer conversations (text, names, phone numbers) | Personal information under POPIA; a leak is a s22 notifiable compromise for the client |
| Company documents (price lists, policies) | Commercially sensitive; one tenant's prices shown to another tenant's customer is a breach and an embarrassment |
| The tenant boundary | The whole product is sold on "your data is yours" |
| Staff inbox sessions | A stolen session reads every conversation of that tenant |
| Model spend and WhatsApp spend | Abuse can run up a bill (cost denial of service) |
| Audit log and CloudTrail | Without them an incident cannot be investigated or disproved |
| The assistant's behaviour | A manipulated assistant that promises a discount creates a liability for the client |

## 2. Trust boundaries

```
 [Customer phone] --Meta--> [EUM Social] --SNS--> [intake Lambda] --> [Step Functions] --> [Bedrock eu-central-1]
 [Visitor browser] --HTTPS/WSS--> [API Gateway] --> [web chat Lambda] ---------------------^
 [Staff browser] --Cognito MFA--> [API Gateway JWT] --> [inbox Lambda]
 all Lambdas --IAM--> [RDS Data API] --> [Aurora, RLS]        [GitHub Actions] --OIDC--> [AWS accounts]
```

TB1 public internet to our APIs; TB2 Meta/AWS to our SNS topic; TB3 our account to Bedrock in
another country; TB4 Lambda to database (tenant boundary enforced here); TB5 CI to AWS; TB6 staff to inbox.

## 3. STRIDE

| ID | Boundary | Threat | Control | Evidence |
|---|---|---|---|---|
| S-01 | TB1 | Spoofed website widget posts as another tenant | Widget key maps to one tenant in SSM; origin allow-list per key | `test_channels.py::test_a_widget_key_only_reaches_its_own_tenant_and_only_from_allowed_origins` |
| S-06 | TB1 | Another website reuses a tenant's public widget key | Key bound to an origin allow-list, checked server-side; CORS is open because no endpoint uses cookies | `test_handlers_wiring.py::test_the_web_chat_handler_answers_a_visitor_from_an_allowed_origin` |
| S-02 | TB2 | Forged WhatsApp event sent to our SNS topic | Topic policy allows only `social-messaging.amazonaws.com` from our account; SNS signature verified in intake | `test_channels.py` (SNS signature tests), DET-08 |
| S-03 | TB6 | Stolen staff password | Cognito MFA required (TOTP), no self sign-up, 12+ char passwords | `stacks.test.ts` MFA assertion |
| S-04 | TB5 | Workflow on a fork or feature branch assumes the prod role | OIDC trust pinned to `environment:prod`, which needs a reviewer | `stacks.test.ts` OIDC assertion |
| S-05 | TB1 | Inbound integration webhook (client system) forged | HMAC-SHA256 with timestamp, 5-minute window, constant-time compare | `test_channels.py::test_webhook_signatures_reject_forgery_and_replay` |
| T-01 | TB4 | Tenant A modifies tenant B's rows | RLS `WITH CHECK`, composite FKs with tenant_id | `test_tenant_isolation.py` |
| T-02 | all | Audit log or event stream rewritten to hide activity | Append-only triggers (even for owner), no UPDATE/DELETE grants, CloudTrail with Object Lock | `test_audit.py`, `scp.test.ts` |
| T-03 | TB5 | Malicious dependency or tampered Lambda package | Trivy, SBOM, pinned actions by SHA, AWS Signer with ENFORCE in prod | CI `security` job, `stacks.test.ts` signing assertion |
| T-04 | TB1 | Poisoned company document injects instructions | Documents need explicit approval; retrieved text is quoted as data; output validated | `evals/redteam` indirect cases |
| R-01 | TB6 | Staff member denies reading a conversation | Reads audited in the same transaction as the read | `test_audit.py::test_who_read...` |
| R-02 | all | "The bot promised me a discount" | Every answer stored with cited chunk ids, route, prompt version | `conversation_events` payloads |
| I-01 | TB4 | Retrieval returns another tenant's chunk | Embeddings in the same RLS-protected rows | `test_tenant_isolation.py` (vector, FTS, module) |
| I-02 | TB3 | Customer PII processed abroad | Redaction before any model call; only redacted text crosses; SCP denies non-EU Bedrock | `test_redaction.py`, `pii_reaching_model` metric = 0 |
| I-03 | all | PII in logs | Semgrep rule bans logging bodies; Step Functions logs no execution data | `frontdesk-message-text-in-logs`, cfn-guard rule |
| I-04 | TB1 | System prompt extracted | Canary token in prompt; validator blocks output containing it | DET-11, red-team leakage cases |
| I-05 | TB6 | Insider bulk-reads transcripts | Per-staff read counter raises DET-05 | `test_inbox_api.py::test_unusual_read_volume_raises_an_event` |
| D-01 | TB1 | Flood of messages runs up Bedrock and WhatsApp cost | Per-number and per-tenant rate limits; stage throttling; monthly token cap; budgets | `test_pipeline.py::test_a_sender_flooding_the_number_is_rate_limited`, DET-09, Budgets |
| D-02 | TB3 | Bedrock outage stops the business | Degraded mode: messages still stored, customer told a person will reply, inbox works | `test_degraded_mode.py`, Step Functions Catch, DET-06 |
| E-01 | TB4 | App role escalates (disable RLS, set role) | App role owns nothing, NOBYPASSRLS, no DDL rights | `test_tenant_isolation.py::test_the_app_role_cannot...` |
| E-02 | all | Workload role creates IAM users or roles | Permission boundary denies `iam:*`; SCP denies users/keys | `stacks.test.ts`, `scp.test.ts` |
| E-03 | TB1 | Model talked into calling a tool outside its scope | Tools take tenant from the transaction, never from model arguments; commitments only via allow-listed tools | `test_agent_tools.py` |

## 4. AI-specific threats

Mapped to OWASP Top 10 for LLM Applications (2025) and MITRE ATLAS in `docs/AI-SECURITY.md`.

| ID | Threat | Control |
|---|---|---|
| AI-01 | Direct prompt injection ("ignore your instructions, 90% off") | Input screening heuristics, Guardrails PROMPT_ATTACK HIGH, topic deny for price negotiation, commitment validator, red-team suite as a gate |
| AI-02 | Indirect injection through an approved document | Approval workflow; documents quoted inside data delimiters; validator rejects answers that contain instructions or URLs not in sources; poisoned-FAQ cases in the suite |
| AI-03 | Hallucinated price, date or policy | No source, no answer; citations must reference retrieved chunks; numeric facts in the answer must appear in cited text (grounding check); Guardrails contextual grounding 0.75 |
| AI-04 | Data leakage across tenants via the model | The model only ever sees one tenant's chunks (RLS); no cross-tenant fine-tuning |
| AI-05 | System-prompt leakage | Canary token, DET-11; prompt contains no secrets |
| AI-06 | Excessive agency | Tools are typed, tenant-scoped, and none can refund, discount or book beyond policy |
| AI-07 | Abuse of a public bot as free general-purpose LLM | Topic restriction in prompt and Guardrails; off-topic requests handed over or refused; rate limits |
| AI-08 | Model misbehaviour after a model or prompt change | Versioned prompts; evals and red-team as build gates; canary deploy with rollback |
| AI-09 | A trained model in the request path takes the service down or leaks text | Shadow mode: redacted input only, no text stored, failures contained; weights rebuilt from committed data in a test (`test_classifier.py`, `test_classifier_training.py`) |
| AI-10 | Markup or a link smuggled into a reply (improper output handling) | Validator rejects URLs not in sources; widget renders text only; WhatsApp link previews off (rt41, rt42) |

## 5. Attack trees

**Goal A: read another tenant's customer conversations.**
1. Via SQL injection in the app: parameterised SQL (Semgrep rule) AND RLS confines any injection to the caller's tenant AND app role cannot disable RLS. Tested.
2. Via a stolen staff session of tenant B: MFA AND short token life (60 min) AND DET-05 read-volume alert. Partially mitigated: an MFA-phished session is possible (accepted risk R-03, FIDO2 recommended when a client pays for Cognito Plus).
3. Via retrieval: chunks behind RLS. Tested.
4. Via the database directly: no network path; Data API needs IAM in our account. Tested by template assertions.

**Goal B: make the assistant commit the client to something.**
1. Direct injection: screened, Guardrails, commitment validator (regex and schema: `commitments` field must be empty unless a tool produced it). Red-team cases.
2. Poisoned document: approval required; validator. Red-team indirect cases.
3. Social engineering ("the manager said I get 50% off"): no source for the claim, so it is handed over.

**Goal C: run up the bill.**
1. Message flood from many numbers: per-tenant cap and token cap stop spend; budgets alarm.
2. Long messages to inflate tokens: input truncated to 2,000 characters before any model call.

## 6. Accepted risks (reviewed each phase)

| ID | Risk | Why accepted now | Revisit when |
|---|---|---|---|
| R-01 | No WAF on CloudFront/API | USD 5+/month; throttling and app limits cover pilot scale | First paying client |
| R-02 | GuardDuty, Security Hub, Config off | Cost; CloudTrail + detections cover the main signals | First paying client |
| R-03 | Cognito Lite: no adaptive authentication, TOTP not phishing-resistant | Plus tier is paid | First paying client |
| R-04 | Default CloudFront certificate allows TLSv1 at the viewer | Needs a custom domain | Custom domain with first client |
| R-05 | Dev database credentials not auto-rotated | Rotation Lambda needs VPC egress (NAT/endpoints cost) | Prod go-live: manual rotation runbook quarterly |
| R-06 | Bedrock processing in the EU | No in-Region Claude/embeddings/Guardrails in af-south-1 | AWS launches them in af-south-1 |
| R-07 | Object Lock in governance mode | Compliance mode is irreversible during the build | First client (ADR 0007) |
