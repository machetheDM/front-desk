# OWASP ASVS Level 2 mapping

Standard: OWASP Application Security Verification Standard 4.0.3, Level 2, mapped at section
level. Each row says how Front Desk meets the section and where the evidence is. **Gap** rows
are honest gaps with a plan; **n/a** rows explain why the section does not apply. Reviewed at
the end of Phase 3 (23 October 2026). Verify section wording against the published standard
when preparing a client security questionnaire.

| ASVS section | How it is met | Evidence |
|---|---|---|
| V1.1 Secure SDLC, threat modelling | STRIDE + AI threats + attack trees, reviewed per phase; ADRs for decisions | `docs/THREAT-MODEL.md`, `docs/adr/` |
| V1.4 Access control architecture | Tenant isolation enforced in Postgres (forced RLS), not only in app code; least-privilege IAM with permission boundaries | `test_tenant_isolation.py`, `stacks.test.ts` boundary test |
| V1.5 Input/output architecture | Customer text treated as data end to end: redaction, tagged prompt sections, schema-validated output, validator before send | `agent/state_machine.py`, `llm/validate.py` |
| V1.14 Configuration architecture | Separate accounts per environment, SCPs, IaC for everything | `infra/`, ADR 0003 |
| V2.1 Password security | Cognito: 12+ characters with complexity; no self sign-up | `app-stack.ts` StaffPool, `stacks.test.ts` |
| V2.2 General authenticator security | MFA required for every staff login; Identity Center MFA for operators | `MfaConfiguration: ON`; `docs/DEPLOY.md` step 4 |
| V2.8 One-time / TOTP | TOTP via Cognito; SMS MFA disabled (SIM-swap risk) | `mfaSecondFactor: { otp: true, sms: false }` |
| V2 (phishing-resistant MFA) | **Gap**: TOTP is phishable; passkeys need Cognito Essentials/Plus | Accepted risk R-03, THREAT-MODEL |
| V3.5 Token-based sessions | Short-lived Cognito ID tokens (60 min) verified by API Gateway; held in memory, not storage | `apps/inbox/lib/session.ts`, ADR 0005 |
| V3.4 Cookie-based sessions | n/a: no application cookies; CORS can therefore be open without CSRF exposure | `app-stack.ts` CORS comment |
| V4.1 General access control | Tenant from the verified immutable token claim; active-staff check under RLS on every request | `inbox_api.principal_from_claims`, `test_inbox_api.py` |
| V4.2 Operation-level (IDOR) | Conversation ids from another tenant read as "not found" because RLS hides them | `test_inbox_api.py::test_a_token_for_another_tenant_cannot_see...` |
| V4.3 Administrative interfaces | Owner-only insights and deletion; prod access through reviewed pipeline only | `inbox_api.py`, `deploy-prod.yml` environment |
| V5.1 Input validation | Pydantic schemas for model output and tool arguments (extra keys forbidden); length limits on messages and replies | `llm/schemas.py`, `agent/tools.py`, `test_agent_tools.py` |
| V5.2 Sanitisation | Widget renders with `textContent`; inbox uses React escaping; no `dangerouslySetInnerHTML` | `apps/widget/widget.js`, `apps/inbox/pages/` |
| V5.3 Injection prevention | Parameterised SQL only (Semgrep rule fails the build); prompt injection layers and red-team gate | `security/semgrep/frontdesk.yml`, `evals/redteam/` |
| V6.2 Algorithms | KMS (AES-256-GCM) at rest, TLS in transit, SHA-256 for SNS signatures (v1 SHA-1 rejected), HMAC-SHA256 webhooks | `sns_verify.py`, `webhook_signature.py` |
| V6.4 Secret management | SSM SecureString and Secrets Manager only; gitleaks with custom rules; no AWS keys anywhere (SCP) | `.gitleaks.toml`, SCP `deny-long-lived-credentials` |
| V7.1 Log content | No message text in logs (Semgrep rule), Step Functions logs no execution data (cfn-guard rule) | `frontdesk-message-text-in-logs`, `FD_STATE_MACHINES_DO_NOT_LOG_CUSTOMER_TEXT` |
| V7.2 Log processing | Security events to EventBridge with runbooks; reads audited | `docs/DETECTIONS.md`, `test_audit.py` |
| V7.3 Log protection | Append-only audit log (trigger, even for owner); CloudTrail to Object Lock bucket; SCP protects trails | `test_audit.py`, `log-archive-stack.ts`, `scp.test.ts` |
| V8.1 General data protection | Data classified (customer text, documents, events); PII redacted before the model | `redact.py`, `docs/POPIA-S72.md` |
| V8.3 Sensitive private data | Deletion reaches messages, derived chunks and embeddings; consent recorded separately for service and marketing | `deletion.py`, `consent.py`, `marketing.py` |
| V9.1 Communications security | HTTPS everywhere (CloudFront redirect, API Gateway TLS, `rds.force_ssl`, S3 `enforceSSL`) | `web-stack.ts`, `data-stack.ts`, cfn-guard |
| V9.1 (TLS 1.2 minimum at viewer) | **Gap**: the default CloudFront certificate permits TLS 1.0 | Accepted risk R-04 until a custom domain |
| V10.3 Deployed application integrity | AWS Signer; prod rejects unsigned Lambda code; pinned action SHAs; SBOM per build | `stacks.test.ts` signing test, `ci.yml` |
| V11.1 Business logic | Rate limits per sender and tenant; monthly model cost cap; commitments impossible by design | `ratelimit.py`, `llm/metering.py`, `llm/validate.py` |
| V12 Files and resources | n/a for customers (no uploads). Operator document upload is local CLI with quarantine scanning | `retrieval/quarantine.py` |
| V13.1 Generic web service | Explicit routes; JWT on all inbox routes; stage throttling | `stacks.test.ts::every inbox route requires the Cognito authorizer` |
| V13.2 RESTful web service | JSON only, content type set, `cache-control: no-store` on API responses | `handlers/common.py`, `inbox_api._json` |
| V14.1 Build and deploy | CI gates: tests, evals, cdk-nag, cfn-guard, Semgrep, gitleaks, Trivy, SBOM, each with a kept failing example | `.github/workflows/ci.yml`, `security/gate-selftest.sh` |
| V14.2 Dependencies | Trivy on lock files and SBOM; pinned versions | CI `security` job |
| V14.4 HTTP security headers | CSP `script-src 'self'`, HSTS preload, frame-ancestors none, nosniff, referrer policy; build fails on inline scripts | `web-stack.ts`, `check-no-inline-scripts.mjs` |
| V14.5 HTTP request header validation / CORS | Open CORS without credentials; widget origin enforced server-side per key | `routing.py`, `test_handlers_wiring.py` |
