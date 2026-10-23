# Depth checklist (acceptance criteria from PROPOSAL.md section 1a)

An item is **done** only when its evidence exists and the link below opens it. **Seam** means
the code is written and tested against fakes, and the remaining step needs an AWS account, a
real client or a paid service; the seam is named. **Open** means not yet built.

Last updated: Phase 5 complete, 23 October 2026.

## AI / ML engineering

| Item | Status | Evidence |
|---|---|---|
| Hybrid retrieval (pgvector + Postgres FTS, reciprocal rank fusion) | done | `backend/frontdesk/retrieval/search.py`; recall@5 0.98 test / 0.88 holdout in `docs/evidence/eval-offline-2026-10-23.md` |
| Reranker | done (offline), seam (Cohere Rerank 3.5) | `retrieval/rerankers.py`; `test_bedrock_client.py::test_rerank_uses_the_frankfurt_cohere_model...` |
| Query rewriting for Sepedi-English code-switching | done (glossary; native-speaker review open) | `data/glossary.yaml`, per-language rows in the eval report |
| Agent as explicit state machine with typed, tenant-scoped tools | done | `agent/state_machine.py`, `agent/tools.py`, `test_agent_state_machine.py`, `test_agent_tools.py` |
| Model routing (small first, large when needed) | done | `llm/router.py`, `test_metering_and_router.py` |
| Prompt caching | done in code, seam (measured savings need Bedrock) | `llm/bedrock.py` cache points; `test_bedrock_client.py::test_system_prompt_and_tools_sit_before_cache_points...` |
| Versioned prompts | done | `llm/prompts/answer.v1.md`, version+hash on every outcome |
| Streaming web replies | done (progress + validated answer over WebSocket; ADR 0009) | `handlers/web_chat.py::ws_handler`, `apps/widget/widget.js`, `app-stack.ts` WebSocketApi |
| Citations enforced (no source, no answer) | done | `llm/validate.py`, `test_validate.py` |
| recall@k and MRR on a labelled set | done | `ml/frontdesk_ml/metrics.py`, eval report |
| Cost and latency per answer by route | done offline; seam for Bedrock cost | `run_evals.py` `by_route`; `llm/metering.py` |

## Phase 2 additions

| Item | Status | Evidence |
|---|---|---|
| Staff inbox with handoff queue, summaries, reply, resolve | done | `inbox_api.py`, `apps/inbox`, `test_inbox_api.py` |
| Cognito MFA, tenant from verified claim | done | `app-stack.ts` (MfaConfiguration ON), `inbox_api.principal_from_claims` |
| Audit of reads demonstrated | done | `test_inbox_api.py::test_reading_a_conversation_is_audited_and_answers_who_read_it` |
| Owner insights computed from events | done | `insights.py`, `test_insights.py` |
| CSP-compatible static inbox | done | `apps/inbox/scripts/check-no-inline-scripts.mjs`, ADR 0005 addendum |

## Phase 3 additions

| Item | Status | Evidence |
|---|---|---|
| WhatsApp intake end to end (verify, route, store, start with ids only) | done; live number is a seam (Meta verification) | `test_whatsapp_and_marketing.py::test_the_intake_lambda...` |
| 24-hour window and approved templates | done; template approval is a seam | `inbox_api.reply`, `send_followup_template`, `channels/whatsapp.py` |
| Consent and opt-out, marketing opt-in separate | done | `consent.py`, `marketing.py`, `test_whatsapp_and_marketing.py` |
| Pinned Python dependencies in SBOM and Trivy | done | `requirements/`, CI `security` job |

## Phase 4 additions

| Item | Status | Evidence |
|---|---|---|
| Tool calls recorded in events (name and outcome, no arguments) | done | `pipeline.persist`, `test_pipeline.py::test_every_tool_call_is_recorded_in_the_event_without_its_arguments` |
| Improper output handling cases (LLM05) in the red-team suite | done | rt41, rt42 in `evals/redteam/cases.yaml` |

## Phase 5 additions

| Item | Status | Evidence |
|---|---|---|
| Restore check: protections survive a restore, not just rows | done | `backend/frontdesk/restore_check.py`, `test_restore.py` |
| Per-tenant kill switch (pause the assistant), audited | done | `backend/frontdesk/controls.py`, `test_controls.py` |
| Immediate staff cut-off and "what did this account read" | done | `backend/frontdesk/staff.py`, `audit.reads_by`, `test_incident_tools.py` |
| Operations dashboard | done | `app-stack.ts` `Operations`, `stacks.test.ts` "operations dashboard" |
| Every PowerShell script and documented block parsed in CI | done | `scripts/ci/check-powershell.ps1`, CI `security` job |
| Breach notice template (POPIA s21 to the client, s22(5) contents) | done | `docs/engagement/breach-notice-template.md` |

## Machine learning beyond the LLM

| Item | Status | Evidence |
|---|---|---|
| Trained handoff/intent classifier compared with rules and LLM | done (rules vs model, with leakage test); LLM arm is a seam (`compare --llm` needs Bedrock) | `docs/evidence/classifier-comparison.md`, `ml/frontdesk_ml/classifier.py`, `ml/tests/test_classifier_training.py`, MODEL-CARD section 6 |
| Served from Lambda | done, in shadow mode (logs, never decides) | `agent/classifier.py` (numpy only), `agent/state_machine.py::_shadow`, `backend/tests/test_classifier.py` |
| Weekly drift on intent mix | done: PSI plus a chi-square test, false-alarm rate measured; weekly Lambda and DET-12 | `backend/frontdesk/drift.py`, `handlers/ops.drift`, `test_drift.py`, `docs/evidence/insights-demo.md` section 4 |
| Model card | done; Bedrock results are a seam | `docs/MODEL-CARD.md` |

## Data science

| Item | Status | Evidence |
|---|---|---|
| Intent clustering (UMAP + HDBSCAN) | done, scored against labelled intents and two baselines; Cohere embeddings are SEAM(bedrock-embeddings-clustering) | `ml/frontdesk_ml/clustering.py`, `ml/tests/test_analyses.py`, insights-demo section 2 |
| Rates with confidence intervals | done | `backend/frontdesk/stats.py` (Wilson), owner report |
| A/B tests of prompt versions | done: sticky assignment, pre-registration with stored hash, power analysis, peeking simulation; live run is SEAM(ab-live-traffic) | `backend/frontdesk/experiments.py`, `experiments/`, `ml/frontdesk_ml/ab.py`, `test_experiments.py`, `test_ab.py` |
| Hour-of-week volume forecast | done: three methods, rolling-origin backtest, MASE 0.86, interval coverage | `ml/frontdesk_ml/forecast.py`, insights-demo section 3 |
| LLM-as-judge calibrated against humans (Cohen's kappa) | seam: tool and kappa done, needs 30 human labels | `ml/frontdesk_ml/judge.py`, `label.py`, `test_gates_and_metrics.py::test_cohens_kappa...` |
| Notebooks/analyses that run in CI on demo data | done (scripted analyses, no notebooks to rot) | `ml/frontdesk_ml/analyses.py`, CI `evals` job step, `docs/evidence/insights-demo.md` |
| Iceberg analytics tables queried with Athena | done in code (nightly export Lambda, no text exported); the first Athena query is a seam (dev account) | `app-stack.ts` (Glue Iceberg table, Athena workgroup), `handlers/ops.export_events`, `test_restore.py::test_the_analytics_export_carries_ids_and_decisions_but_no_text` |

## Cloud security

| Item | Status | Evidence |
|---|---|---|
| Organizations: management, security, log-archive, dev, prod | done in code, seam (owner deploys) | `infra/lib/org/organization-stack.ts`, `stacks.test.ts` "organization" |
| SCPs in code with tests | done | `infra/policies/scp/*.json`, `infra/test/scp.test.ts` (17 tests) |
| IAM Identity Center with MFA, no IAM users | done (runbook), SCP enforced | `docs/DEPLOY.md` steps 4, 5, 10; SCP `deny-long-lived-credentials` |
| Permission boundaries on every role | done | `github-oidc-stack.ts` boundary; `stacks.test.ts::every role in a workload stack carries the permission boundary` |
| Database not reachable from the internet; Data API only | done | `data-stack.ts`; `stacks.test.ts` "Aurora is private", "no internet gateway and no NAT"; ADR 0004 |
| KMS with tenant encryption context | key done; per-object tenant context is a seam until documents are uploaded through S3 (Phase 6 onboarding) | `data-stack.ts` |
| Org CloudTrail to log-archive with Object Lock | done in code | `org-trail-stack.ts`, `log-archive-stack.ts`, ADR 0007 |
| IAM Access Analyzer (external access) | done in code | `org/security-stack.ts`, DET-10 |
| Access Analyzer findings at zero | seam: needs deployed org | |

## Cyber security

| Item | Status | Evidence |
|---|---|---|
| STRIDE threat model + attack trees | done (draft, reviewed per phase) | `docs/THREAT-MODEL.md` |
| WhatsApp/SNS and webhook signature verification | done | `channels/sns_verify.py` (v2 only), `channels/webhook_signature.py`, `test_channels.py` |
| Per-number and per-tenant rate limits | done | `ratelimit.py`, `test_pipeline.py::test_a_sender_flooding_the_number_is_rate_limited` |
| Detection rules with runbooks, firing in tests | done | `docs/DETECTIONS.md`; `infra/test/detections.test.ts` |
| OWASP ASVS L2 checklist mapped to code | done | `docs/ASVS.md` |
| DAST (OWASP ZAP) against dev | workflow done, seam (needs the dev site) | `.github/workflows/zap.yml` |
| Recorded incident drill | done: model-down drill and backup/restore drill run on every PR; stolen-session tabletop; AWS runs are SEAM(drill-model-down-aws) and SEAM(restore-drill-aws) | `docs/drills/`, `test_drills.py`, `test_restore.py`, `scripts/restore-drill.ps1` |

## DevSecOps

| Item | Status | Evidence |
|---|---|---|
| GitHub Actions with OIDC, no stored keys | done in code | `.github/workflows/`, `github-oidc-stack.ts` |
| cdk-nag and cfn-guard on every synth | done | CI `infra` job; `nag-gate.test.ts`; `policies/guard/tests/frontdesk_tests.yaml` |
| Semgrep, gitleaks, Trivy, SBOM | done | CI `security` job; `security/semgrep/frontdesk.yml`; `.gitleaks.toml` |
| A failing example kept for every gate | done | `security/gate-selftest.sh` (CI `gate-selftest`) |
| Lambda code signing (AWS Signer), prod refuses unsigned | done in code | `app-stack.ts`; `stacks.test.ts::prod refuses unsigned Lambda code` |
| Canary deploys with automatic rollback | done in code, seam (demonstration needs dev) | `app-stack.ts` CodeDeploy group; `stacks.test.ts::released by canary` |
| Evals and red-team as build gates | done | CI `evals` job; `ml/frontdesk_ml/gates.py` |
| DORA metrics | done: weekly workflow; values appear once deploys exist | `tools/dora/dora.py`, `.github/workflows/dora.yml`, `test_dora.py` |

## Data and AI security

| Item | Status | Evidence |
|---|---|---|
| OWASP LLM Top 10 and MITRE ATLAS mapping | done: all ten OWASP risks, twelve ATLAS techniques, checked by a test | `docs/AI-SECURITY.md`, `ml/tests/test_redteam_suite.py::test_ai_security_maps_all_ten_owasp_risks_and_every_tag_the_suite_uses` |
| Layered defence (input checks, Guardrails, PII, grounding, schema, tool scope) | done; Guardrails is a seam | `agent/policy.py`, `ai-stack.ts`, `llm/guardrails.py`, `llm/validate.py`, `agent/tools.py` |
| Indirect injection through a poisoned FAQ | done | `retrieval/quarantine.py`, `test_quarantine.py`, red-team rt27 to rt29 |
| Canary tokens for prompt leakage | done | `llm/prompts.py`, validator, DET-11 |
| PII redacted before the model and in logs | done (model); logs: Semgrep rule | `redact.py`, `test_redaction.py`, `frontdesk-message-text-in-logs` rule |
| POPIA deletion reaching transcripts, chunks, embeddings | done | `deletion.py`, `test_inbox_api.py::test_a_deletion_request_previews_then_erases...` |
| Red-team suite (promptfoo) in CI with attack success rate | done offline (0 of 42); promptfoo export done, release run is a seam | `ml/frontdesk_ml/redteam.py --export-promptfoo`, `evals/redteam/cases.yaml` |

## Forward deployed / architecture / consulting

| Item | Status | Evidence |
|---|---|---|
| ADRs for real decisions | done (8) | `docs/adr/` |
| Replay owner report | done | `docs/evidence/replay-demo-offline/owner-report.md` |
| Discovery workshop, pilot charter, ROI sheet, handover pack, case study | Phase 6 | |
| Well-Architected review | done: six pillars and the Generative AI Lens, no open HRI | `docs/WELL-ARCHITECTED.md` |
| One real integration behind an adapter | adapter done, seam (needs a client) | `integrations/orders.py` (`HttpOrdersAdapter`, SEAM(client-order-api)) |
