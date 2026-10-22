# ADR 0002: Data stays in af-south-1; Bedrock runs in eu-central-1, never via Global routing

- Status: accepted
- Date: 2026-10-22

## Context
Region matrix (docs/ARCHITECTURE.md section 2), from the AWS Price List API on 22 October 2026:

- af-south-1 sells Claude Haiku 5.5 and Sonnet 5.5 **only as Global cross-Region inference**.
  Global routing may process a request in any commercial AWS Region. We could not tell a client
  which country their customers' messages were processed in.
- af-south-1 sells **no** Bedrock embeddings, rerank or Guardrails.
- eu-central-1 (Frankfurt) sells all of them: Claude Haiku/Sonnet 5.5 (non-Global SKUs at about
  10% above Global), Cohere Embed Multilingual and v4, Titan Embeddings V2, Cohere Rerank 3.5,
  Amazon Rerank 1.0, Guardrails. eu-west-1 (Ireland) lacks rerank.

POPIA s72 allows a transfer when the recipient is subject to law that gives adequate protection.
The GDPR in Germany is the clearest case available to us. "Anywhere in the world" is not.

## Decision
- All data at rest, compute and messaging: af-south-1.
- All Bedrock calls: eu-central-1, in-Region model IDs where offered, otherwise the `eu.` geo
  inference profile (EU Regions only). `scripts/check-regions.ps1` records which IDs exist.
- An SCP denies `bedrock:*` in every Region except eu-central-1, so a Global call from
  af-south-1 is impossible even by mistake.
- Only redacted text crosses (PII placeholders applied first). Bedrock model invocation logging
  stays off; our own logs stay in af-south-1.

## Consequences
- Each answer pays about 160 to 200 ms extra round trip Cape Town to Frankfurt. Acceptable for
  WhatsApp; measured in Phase 2.
- About 10% more per token than Global. At pilot volume this is cents.
- Revisit when AWS offers in-Region Claude, embeddings and Guardrails in af-south-1.
