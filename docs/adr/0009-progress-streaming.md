# ADR 0009: Stream progress and validated answers, never unvalidated model tokens

- Status: accepted
- Date: 2026-10-23

## Context
The proposal asked for streaming web replies. Token streaming (sending the model's words as
it produces them) feels fast, but every safety property of Front Desk is checked on the
*complete* answer: citations exist, every rand amount is in a cited source, no commitment
language, no link, no canary, Guardrails grounding. A streamed token cannot be un-sent. A
customer would see "Yes, we can do 20% off" before the validator rejected it.

## Decision
The web chat uses an API Gateway WebSocket. The server sends a status message immediately
("Checking our information..."), runs the full pipeline including validation, and then sends
the validated answer in sentence-sized parts, followed by `done` with the citations. The
HTTP endpoint stays as a fallback for clients without WebSockets. WhatsApp has no streaming.

## Consequences
Time to first visible text is the status message (well under a second); time to the answer
is the full pipeline latency (measured per route in the eval report). We give up the
"typing" effect of token streaming in exchange for never showing a customer something the
validator would have stopped. Revisit if Bedrock offers streaming with incremental guardrail
and grounding checks that we can verify ourselves.
