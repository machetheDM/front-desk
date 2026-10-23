# Phase 3: WhatsApp and detection, in plain language

## 1. How a WhatsApp message reaches us

Meta delivers the message to AWS End User Messaging Social, which publishes it to our SNS topic
in Cape Town; SNS invokes the intake Lambda. Three checks happen before anything else: the SNS
signature (version 2, SHA-256; SHA-1 version 1 is rejected), the topic it came from, and which
tenant the business number belongs to (an SSM lookup, because no tenant context exists yet).
The message is stored, and Step Functions starts with ids only. A Meta retry carries the same
message id, which we store, so a retry never gets a second answer.

> Interview line: "WhatsApp events are signature-verified, de-duplicated by Meta's message id,
> routed to a tenant before any database access, and the workflow carries only ids."

Check yourself: why can't the tenant for an inbound WhatsApp message be looked up in the main database?

## 2. WhatsApp's own rules

Meta only allows free-text business replies within 24 hours of the customer's last message.
After that, only pre-approved templates. The inbox enforces it: a staff reply outside the
window is refused with an explanation, and a "send follow-up template" action sends the
business's approved utility template instead. Marketing templates are a different category,
cost more, and need marketing consent.

> Interview line: "Platform rules are enforced in code, not left to staff memory: the inbox
> knows the 24-hour window and switches to the approved template."

Check yourself: what is the difference between a utility and a marketing template, in cost and in consent?

## 3. Consent, separately (POPIA s69)

Writing to a business gives it permission to answer you, not to market to you. Two records:
`service` (granted when the customer starts the conversation, withdrawn by STOP) and
`marketing` (only from an exact keyword like JOIN UPDATES; "yes" or "thanks" never counts).
The only query that selects marketing recipients is `marketing.audience()`, which requires the
*latest* marketing record to be a grant, so STOP after JOIN removes the person immediately.

> Interview line: "Marketing audience selection exists in exactly one query that requires an
> explicit, latest-wins opt-in; a test proves answered customers and opted-out customers are excluded."

Check yourself: why is the consents table append-only instead of a single flag per customer?

## 4. Detections that are tested, not just deployed

The CloudTrail detections (root activity, IAM changes outside the pipeline, trail tampering,
console sign-in without MFA) are CloudWatch Logs filter patterns. A small evaluator in the
tests runs sample CloudTrail events through the real patterns: root sign-in fires DET-01, but an
AWS service acting as root does not; an IAM change by the CDK deploy role does not fire DET-02,
by a person it does. Another test checks that every metric an alarm watches and every event a
rule listens for is actually emitted somewhere in the Python code.

> Interview line: "My detections have unit tests: sample CloudTrail events go through the real
> filter patterns, including the negative cases that would otherwise page me at 3 a.m."

Check yourself: what silent failure does the "every alarmed metric is emitted" test catch?

## 5. ASVS and DAST

`docs/ASVS.md` maps OWASP ASVS Level 2 sections to code and tests, including honest gaps
(TOTP is phishable; the default CloudFront certificate allows old TLS). The ZAP baseline scan
runs weekly against the dev site once it exists.

> Interview line: "I can hand a client's security questionnaire an ASVS L2 mapping where every
> 'yes' links to a test, and every 'no' is an accepted risk with a trigger to revisit."

Check yourself: why is a passive ZAP baseline safe to run against a live environment, and what does it miss?
