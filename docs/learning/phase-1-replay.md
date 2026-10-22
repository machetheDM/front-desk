# Phase 1: the replay, in plain language

## 1. From documents to chunks

A company's price list, warranty and FAQ are Markdown files. The chunker cuts them along their
own structure: one chunk per FAQ answer, one per price-list row. A table row keeps its column
names ("Package: Home 5kW backup. Installed price: R62 900."), so a price is never separated
from the product it belongs to. Each chunk gets an embedding (a list of 1,024 numbers that
places similar meanings close together) and a full-text index entry, both in the same
Postgres row as the text, behind the same row-level security.

Loading documents is a `WritePlan`: it shows what it would write (`--dry-run`), and when you
say `--write` it checks again, inside the writing transaction, that the target is the right
tenant and the document is not already loaded.

> Interview line: "Chunks follow the document's structure and table rows keep their headers,
> so a retrieved price is self-explanatory when quoted as a citation."

Check yourself: why is a document not retrievable just because it was loaded?

## 2. Hybrid retrieval

Two searches run on every question. Vector search finds paraphrases ("what does it cost" vs
"price"); full-text search finds exact tokens ("5kW", "Seshego") that embeddings blur. Reciprocal
rank fusion adds 1/(60 + rank) from each list, which merges them without comparing their raw
scores. A reranker then reads each candidate together with the question and reorders them.
Customers mix Sepedi and English ("theko ya 5kW ke bokae?"), so a glossary adds the English
words ("price", "how much") to the query before searching; it only adds, never removes.

> Interview line: "I fuse pgvector and Postgres full-text results with reciprocal rank fusion
> and rerank, and I measure it: recall@5 0.96 on the test split and 0.88 on a clean holdout."

Check yourself: what would go wrong if you added the two searches' raw scores together?

## 3. The agent is a state machine

Every message goes through named states: screen (redact personal data, catch opt-outs and
injections), policy (rules that always hand over: a person was asked for, a discount, a
complaint), tools (order status, service area, callback request), retrieve, route (small or
large model), draft, validate, then send or hand over. The runner refuses any transition that
is not in the table, and the path taken is stored with the answer, so "why did it say that?"
has an exact answer. If the model fails at any point, the state is DEGRADED: the customer is
told a person will reply and the conversation goes to staff.

> Interview line: "The agent is an explicit state machine with a transition table the runner
> enforces; an injection provably never reaches the draft state, and a model outage becomes a
> handoff instead of an error."

Check yourself: why does the order-status tool give the same reply for "not your order" and "no such order"?

## 4. Validation: the last line before the customer

Whatever drafts the answer, the validator checks it: at least one citation; every citation
points to a chunk that was actually retrieved; every quote is really in that chunk; every
number in the answer (rand amounts, kW, years, times) appears in a cited chunk; no links; no
commitment language ("we will give you a discount"); no guessed title ("Mrs"); and no system
prompt canary. A failed draft gets one retry on the larger model, then goes to a person.

> Interview line: "Groundedness is enforced in code, not requested in the prompt: a number the
> customer reads must exist in a cited source, or the answer is not sent."

Check yourself: name two things the validator catches that a "be accurate" prompt would not.

## 5. Measuring it honestly

The labelled set has a **dev** split (thresholds tuned on it), a **test** split (gated in CI)
and, written afterwards, a **holdout** run once. The first run printed test-split failures, so
test is no longer clean; the holdout is the honest number. The offline baseline scored 0.78
answer correctness on test but 0.52 on the holdout: the heuristic that decides "is this
answerable?" does not generalise. Retrieval did (0.88 recall@5). ADR 0008 records this and
turns the offline floors into regression floors. Rates in the owner report carry 95% Wilson
intervals because 212 questions is a small sample.

> Interview line: "I kept a holdout written after tuning; it showed my heuristic baseline
> dropping from 0.78 to 0.52 correctness, so I documented it as a regression floor rather than
> a quality claim. That is the gap a real LLM pipeline has to close, measured the same way."

Check yourself: what is the difference between the test split and the holdout here, and why does it matter?

## 6. The red-team suite

Forty attacks, each tagged with an OWASP Top 10 for LLM Applications id and a MITRE ATLAS
technique: direct and indirect injection, jailbreaks, system-prompt extraction, commitment
traps, tool abuse (someone else's order), cross-tenant probing (a second tenant with secret
prices), PII, encoding tricks, Sepedi and Afrikaans injections. A poisoned FAQ document is
loaded: instruction-like text and links are quarantined at ingestion; an unapproved document is
never retrieved. Attack success rate is 0 of 40 on the offline pipeline, gated at 0.

> Interview line: "I test indirect prompt injection by poisoning the knowledge base itself;
> chunks that address the AI or carry links are quarantined at ingestion and never retrieved."

Check yourself: which red-team cases can promptfoo not run against the deployed API, and why?

## What you ran

```powershell
docker compose up -d db
$env:FRONTDESK_TEST_ADMIN_DSN = "postgresql://postgres:local-only@localhost:5433/postgres"
python -m frontdesk_ml.run_evals --pipeline offline --out reports\local
python -m frontdesk_ml.gates --pipeline offline --metrics reports\local\metrics.json
Get-Content reports\local\eval-report.md
```
