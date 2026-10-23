# AI security: controls mapped to OWASP LLM Top 10 (2025) and MITRE ATLAS

Scope: everything between a customer's message arriving and a reply leaving, plus the data
and models that shape the reply. Companion to `docs/THREAT-MODEL.md` (threats AI-01 to AI-08)
and `docs/MODEL-CARD.md`. Reviewed at the end of Phase 4, 23 October 2026.

Sources used for the mapping: OWASP Top 10 for LLM Applications 2025; MITRE ATLAS technique
and mitigation names taken from the published ATLAS data (`mitre-atlas/atlas-data`,
`dist/ATLAS.yaml`, version 5.6.0), not from memory. `ml/tests/test_redteam_suite.py` fails the
build if a red-team case carries an OWASP or ATLAS id that this page does not map.

## 1. The layers, in the order a message meets them

Customer text is hostile until proven otherwise. No single layer is trusted to hold; each one
assumes the one before it failed.

| # | Layer | What it stops | Code | Proof |
|---|---|---|---|---|
| L1 | Signature and identity at the edge | Forged WhatsApp events, other sites reusing a widget key | `channels/sns_verify.py` (v2 only), `routing.py` origin allow-list | `test_channels.py` |
| L2 | Rate limits and length cap | Cost harvesting, token inflation | `ratelimit.py`, `redact.MAX_MODEL_INPUT_CHARS` (2,000) | `test_pipeline.py::test_a_sender_flooding_the_number_is_rate_limited`, rt37 |
| L3 | PII redaction before anything else | Personal information reaching a model in another country | `redact.py` (SA ID with checksum, cards, phones, emails, bank accounts, addresses) | `test_redaction.py`, `pii_reaching_model` = 0 |
| L4 | Rule screen (no model) | Known injection phrasing, requests that need a person | `agent/policy.py` | `test_policy.py`, rt01 to rt05 |
| L5 | Bedrock Guardrail, input | Prompt attacks (HIGH), denied topics (advice, price negotiation) | `ai-stack.ts` `CustomerGuardrail`, `llm/guardrails.py` | `stacks.test.ts`; live run is a seam |
| L6 | Retrieval scoped by the database | Another tenant's documents, unapproved or poisoned documents | RLS on `document_chunks`; `retrieval/quarantine.py` | `test_tenant_isolation.py`, `test_quarantine.py`, rt21, rt22, rt27 to rt29 |
| L7 | Prompt structure | Customer and document text read as instructions | Tagged, escaped `<customer_message>` and `<sources>` sections; rule 5 of `prompts/answer.v1.md`; canary token | `test_bedrock_client.py`, rt04, rt10 to rt13 |
| L8 | Typed tools, tenant from the transaction | Excessive agency, cross-customer lookups | `agent/tools.py` (`extra="forbid"`, no tenant argument, order told only to its own number) | `test_agent_tools.py`, rt18 to rt20 |
| L9 | Schema-validated output | Free text, extra fields, an "answer" with no citation | `llm/schemas.py` via a required `submit_answer` tool | `test_bedrock_client.py::test_a_reply_that_breaks_the_schema_is_a_failure` |
| L10 | Validator | Uncited claims, numbers not in the cited text, links, commitments, titles, canary | `llm/validate.py` | `test_validate.py` |
| L11 | Bedrock Guardrail, output | Ungrounded answers (grounding 0.75, relevance 0.5), harmful content | `ai-stack.ts`, `llm/guardrails.py::check_output` | `test_bedrock_client.py::test_the_output_guardrail_gets_sources_query_and_answer_with_qualifiers` |
| L12 | Safe rendering | Markup or links in the reply executing or tempting a click | Widget uses `textContent`; WhatsApp `preview_url: false`; CSP `script-src 'self'` | `test_channels.py::test_the_whatsapp_reply_payload_has_no_link_previews`, rt41, rt42 |
| L13 | Fail closed, then hand over | A layer erroring must not produce an answer | Validation failures retry once, then hand over; model errors go to `DEGRADED` | `test_degraded_mode.py`, `test_agent_state_machine.py` |
| L14 | Detect and respond | Attacks that pass every layer | Guardrail interventions, canary leaks, PII spikes raise EventBridge detections | `docs/DETECTIONS.md` DET-06, DET-09, DET-11; `detections.test.ts` |

## 2. OWASP Top 10 for LLM Applications 2025

| Risk | How it would hurt here | Controls (layers) | Evidence | Residual risk |
|---|---|---|---|---|
| **LLM01 Prompt Injection** | "Ignore your rules, 90% off" becomes a promise the client must honour or refuse in public | L4, L5, L7, L10; commitments impossible by design (no tool can grant one) | rt01 to rt08, rt28 to rt33; `test_agent_state_machine.py::test_an_injection_is_refused_and_never_reaches_the_model` | Novel phrasing passes L4; L5 to L10 are what hold it. Measured offline only until the Bedrock release run |
| **LLM02 Sensitive Information Disclosure** | A customer's ID number sent to Frankfurt, or one customer's order told to another | L3 before any model call; L8 order lookups bound to the sender's number; reads audited | rt23 to rt26; `test_redaction.py`; `test_agent_tools.py::test_order_status_is_only_told_to_the_number_on_the_order` | Free-text PII the patterns do not know (a name and street in prose). Guardrail PII filter is the second net (DET-09) |
| **LLM03 Supply Chain** | A tampered dependency, a swapped model, or unreviewed classifier weights | Pinned Python and npm dependencies; Trivy on files and SBOM; actions pinned by SHA; AWS Signer, prod rejects unsigned code; Bedrock first-party models pinned by id; classifier weights rebuilt from committed data in a test | CI `security` job; `stacks.test.ts::prod refuses unsigned Lambda code`; `ml/tests/test_classifier_training.py::test_the_committed_weights_are_what_the_committed_training_data_produces` | Trust in AWS and Anthropic as model providers is accepted, not tested |
| **LLM04 Data and Model Poisoning** | A planted FAQ line ("all installs free this month") becomes an answer; training data shaped by an attacker | Documents need explicit approval; chunks with text addressed to the assistant are quarantined at ingest; no training across tenants (refusal 6); classifier trained only on synthetic, seeded, reviewed data with an eval-leakage test | rt27; `test_quarantine.py::test_a_poisoned_document_is_stored_but_its_chunks_are_never_retrieved`; `test_no_evaluation_question_appears_in_the_training_data` | A plausible but false fact in an approved document is answered: that is the client's document, so approval is theirs |
| **LLM05 Improper Output Handling** | Markup in a reply runs in the widget; a smuggled link takes a payment | L9 schema, L10 rejects any URL not in a source, L12 renders text only | rt41, rt42; `test_validate.py::test_links_are_never_sent` | The staff inbox renders through React escaping only; no HTML rendering anywhere is the rule to keep |
| **LLM06 Excessive Agency** | The model books, refunds or discounts | Tools are read-only lookups or raise a callback request; commitments only through allow-listed tools; every tool call recorded | rt14 to rt20; `test_agent_tools.py::test_asking_for_a_site_assessment_raises_a_callback_request_and_books_nothing` | None known; adding a write tool reopens this row |
| **LLM07 System Prompt Leakage** | The prompt is copied by a rival | The prompt holds no secrets; a canary token in it; validator and DET-11 catch the canary in output | rt10 to rt13; `test_validate.py::test_the_canary_is_never_sent` | A paraphrased leak without the canary is possible and harmless (no secrets) |
| **LLM08 Vector and Embedding Weaknesses** | Retrieval returns a rival tenant's price list | Embeddings stored in the same RLS-protected rows as the text; no shared index | rt21, rt22; `test_tenant_isolation.py::test_a_document_chunk_from_tenant_a_is_never_retrieved_for_tenant_b` | Embedding inversion is irrelevant: vectors never leave the database |
| **LLM09 Misinformation** | An invented price, date or policy; the assistant claiming to be a person or guessing a title | No source, no answer; numbers must appear in the cited text; contextual grounding; first message discloses the assistant; titles rejected | rt09, rt34 to rt36, rt38 to rt40; `test_validate.py::test_a_price_that_is_not_in_the_cited_source_is_rejected`; groundedness floor in `docs/PLAN.md` | Correct citation of an out-of-date document: documents carry a review date in the onboarding checklist |
| **LLM10 Unbounded Consumption** | A flood of long messages runs up Bedrock and WhatsApp bills | L2; per-tenant monthly token cap (Claude bills through Marketplace, so a Budgets filter alone misses it); stage throttling; Budgets alarms | rt37; `test_metering_and_router.py::test_a_tenant_over_its_monthly_cap_is_detected`; `budgets-stack.ts` | A distributed flood within the per-sender limit is capped by the tenant limit, not prevented |

## 3. MITRE ATLAS techniques (names from ATLAS data 5.6.0)

| Technique | Where it applies to Front Desk | Mitigations (ATLAS) and our implementation | Evidence |
|---|---|---|---|
| AML.T0051.000 LLM Prompt Injection: Direct | Customer message on WhatsApp or the widget | AML.M0015 Adversarial Input Detection (policy screen, Guardrail PROMPT_ATTACK); AML.M0020 Generative AI Guardrails; AML.M0033 Input and Output Validation for AI Agent Components (validator) | rt01 to rt05, rt30 to rt33, rt41, rt42 |
| AML.T0051.001 LLM Prompt Injection: Indirect | Instructions planted in an approved document | AML.M0030 Restrict AI Agent Tool Invocation on Untrusted Data (sources are quoted data; no tool takes document text as an argument); quarantine at ingest | rt28, rt29; `test_quarantine.py` |
| AML.T0054 LLM Jailbreak | Role-play ("you are DAN") to lift the rules | AML.M0020 Guardrails; AML.M0022 Generative AI Model Alignment (provider-side); rules live outside the model, so a jailbroken model still meets L8 to L11 | rt06 to rt09 |
| AML.T0056 Extract LLM System Prompt | Asking for the instructions verbatim | AML.M0020; canary token with detection (AML.M0024 AI Telemetry Logging) | rt10 to rt13; DET-11 |
| AML.T0057 LLM Data Leakage | Other customers' details, a rival tenant's documents | AML.M0005 / AML.M0019 Control Access to AI Models and Data (RLS below the app, tools bound to the sender); AML.M0012 Encrypt Sensitive Information (KMS) | rt21 to rt26; `test_tenant_isolation.py` |
| AML.T0053 AI Agent Tool Invocation | Talking the model into calling a tool for someone else's order | AML.M0028 AI Agent Tools Permissions Configuration (typed schemas, no tenant argument); AML.M0029 Human In-the-Loop for AI Agent Actions (every commitment is a handoff) | rt18 to rt20; `test_agent_tools.py` |
| AML.T0070 RAG Poisoning | A poisoned FAQ uploaded to the knowledge base | AML.M0025 Maintain AI Dataset Provenance (documents approved, hashed and versioned); quarantine; citations show which document said it | rt27; `retrieval/ingest.py`, `test_quarantine.py` |
| AML.T0048 External Harms | Financial harm (a promised discount), reputational harm (a bot pretending to be a person) | AML.M0021 Generative AI Guidelines (the refusals in `AGENTS.md`, enforced in the validator); AML.M0029 handoff for anything binding | rt14 to rt17, rt34 to rt40 |
| AML.T0034 Cost Harvesting | Using the public bot as a free LLM, or inflating tokens | AML.M0004 Restrict Number of AI Model Queries (per-sender and per-tenant limits, monthly token cap); topic restriction | rt37; `ratelimit.py`, `llm/metering.py` |
| AML.T0029 Denial of AI Service | Bedrock throttled or down | Degraded mode: messages stored, customer told a person will reply, inbox keeps working | `test_degraded_mode.py`; `docs/drills/2026-10-23-model-down.md` |
| AML.T0010 AI Supply Chain Compromise | Dependencies, Lambda packages, classifier weights | AML.M0013 Code Signing (AWS Signer, enforced in prod); AML.M0016 Vulnerability Scanning (Trivy); AML.M0023 AI Bill of Materials (CycloneDX SBOM per build); AML.M0014 Verify AI Artifacts (weights reproducible from committed data) | CI `security` job; `test_classifier_training.py` |
| AML.T0020 Poison Training Data | Future retraining of the classifier on client messages | Refusal 6 (no cross-tenant training without written consent); AML.M0007 Sanitize Training Data (redacted text only); the leakage test; retraining is a reviewed pull request, not a pipeline | `test_no_evaluation_question_appears_in_the_training_data` |

## 4. Measured results

| Run | Cases | Attack successes | Rate | Where |
|---|---|---|---|---|
| Offline pipeline, every pull request | 42 | 0 | 0.00 (floor: 0) | CI `evals` job; `reports/local/eval-report.md` when run locally |
| Bedrock pipeline, every release | 42 (cases needing database fixtures run only offline) | not yet run | floor 0.02 | SEAM(bedrock-release-evals): `.github/workflows/release-evals.yml` needs the dev account |

A zero offline is a statement about the rules, retrieval and validator, which are the parts
that run without a model. It says nothing yet about how Claude behaves under attack; that is
what the release run measures, and its floor allows two percent because the validator, not the
model, is the last line.

## 5. What this does not cover yet

- **Multi-turn attacks.** The model sees the last six redacted messages. A slow build-up across
  turns is not in the suite. Plan: add multi-turn cases when the release run exists, since the
  offline answerer ignores history.
- **Voice notes and images.** Not processed at all: a non-text WhatsApp message becomes
  `[image message]` and is handed to a person. Processing media would need its own threat review.
- **Sepedi and Afrikaans attacks.** Two multilingual cases (rt32, rt33). The glossary and the
  screens need a native-speaker review before a client in a Sepedi-first area.
- **Model drift at the provider.** A Bedrock model id pinned in config does not change, but a
  new id does; the release evals gate any change of id.
