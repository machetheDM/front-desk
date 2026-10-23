# Case study (template)

Published only with the client's written permission, after they have read the final text.
Numbers come from the conversation events and the client's own records, each with its interval
or its source. No customer is quoted or identifiable.

**Permission:** [ ] The client has approved this text for publication on [date], signed by [name, role].
**Can we name the business?** [ ] yes [ ] no, describe it as "[an installer in Limpopo]"

## The business

[One paragraph: what they sell, where, how many staff, how customers reach them.]

## The problem, in their numbers

[From the discovery workshop and the replay, before the pilot: messages a week, share after
hours, how many waited until morning, the owner's own words about what that cost them.]

## What was done

- Replay of [N] real questions from [month] on their documents, before any contract.
- 30-day pilot on [channel], [n] staff, [n] documents, [integration if any].
- Built-in limits the client chose to keep: [e.g. no discounts, all complaints to the owner].

## What happened (30 days)

| Measure | Before | During the pilot | Source |
|---|---|---|---|
| Messages answered at once after hours | [x] | [y] ([interval]) | event stream |
| Routine questions staff answered by hand, a week | [x] | [y] | event stream; staff estimate |
| Quotes or sales from WhatsApp leads | [x] | [y] | the client's records |
| Commitments made by the assistant | n/a | 0 | validator, red-team suite |

## What did not work

[Required. Questions it handed over that it should have answered, wording it misunderstood,
anything staff disliked, and what was changed because of it.]

## In their words

["Quote from the owner, approved by them."]

## How it was built

Single-tenant data isolation in the database, redaction before any model, answers only from
approved documents with citations, an evaluation and red-team suite as build gates, and an
operator who can pause it at once. Details: the public repository.
