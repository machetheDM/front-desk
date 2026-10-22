# POPIA section 72: transfer of personal information outside South Africa

Status: assessment for the pilot, 22 October 2026. To be attached to each client's pilot
charter. Not legal advice; the client's Information Officer signs off.

## The flow

| | |
|---|---|
| What leaves SA | Customer message text **after redaction** (SA ID numbers, card numbers, bank account numbers, phone numbers and e-mail addresses replaced by placeholders), the company's own document excerpts retrieved for that question, and a short conversation summary for handoffs |
| What does not leave SA | Customer phone numbers and names (they never enter a prompt: the assistant is told the name only if the customer gave one, and the phone number never), stored conversations, documents, embeddings at rest, audit logs, backups |
| From | Front Desk Lambda functions in AWS af-south-1 (Cape Town) |
| To | Amazon Bedrock in AWS eu-central-1 (Frankfurt), with possible routing to other EU Regions when an `eu.` inference profile is used. Never Global routing: an SCP denies it (ADR 0002) |
| Recipient | Amazon Web Services EMEA SARL / AWS as processor under the AWS Customer Agreement and Data Processing Addendum |
| Retention at recipient | Bedrock does not store prompts or completions and does not use them to train models (AWS Bedrock data-protection documentation; re-confirm at each review). Model invocation logging is **off** in our accounts |

## Ground for transfer: s72(1)(a)

The recipient is subject to a law (the EU General Data Protection Regulation) that provides
an adequate level of protection: it upholds principles for reasonable processing substantially
similar to POPIA's conditions, including further-transfer restrictions. In addition, the AWS
DPA binds AWS contractually (relevant to s72(1)(b)), and the processing is necessary for the
service the data subject asked for when they messaged the business (s72(1)(c) and (d) context).

## Why not keep it in South Africa

As at 22 October 2026, af-south-1 offers Claude only through **Global** cross-Region inference
(processing may occur in any commercial AWS Region, so we could not name the country), and
offers no embeddings, reranking or Guardrails (docs/ARCHITECTURE.md section 2). Frankfurt is
the nearest Region with all of them under a single, named, adequate jurisdiction.

## Minimisation measures (tested)

- Redaction before every model call: `backend/frontdesk/redact.py`, `backend/tests/test_redaction.py`; release metric `pii_reaching_model` must be 0 (docs/PLAN.md).
- Input truncated to 2,000 characters.
- Guardrails sensitive-information filters as a second layer.
- SCP and IAM conditions restrict Bedrock calls to EU Regions; Semgrep rule bans Global profile ids in code.

## Review triggers

AWS launches in-Region Claude, embeddings or Guardrails in af-south-1; AWS changes Bedrock data
handling terms; a client in a regulated sector; any change to what enters a prompt.
