# Phase 5: insights and production, in plain language

What was run: `python -m frontdesk_ml.analyses` (topics, forecast, drift, A/B planning, written to
`docs/evidence/insights-demo.md`), `python -m frontdesk.drills backup-restore`, the replay of the
demo tenant's month (`python -m frontdesk.replay`), the offline evals (refreshed evidence
dated 23 October), and the full test suites. All of it also runs in CI.

## 1. Grouping what the documents are missing

Forty unanswered questions are hard to act on; five topics with a count and an example are a
to-do list. Each question becomes a vector, UMAP squeezes the vectors down to five dimensions
while keeping neighbours together, and HDBSCAN finds dense groups and calls the leftovers noise
instead of forcing them into a topic. Each topic is named by the words that are common in it and
rare elsewhere (class-based TF-IDF). On the labelled questions it beat k-means even though
k-means was told the right number of intents, and without UMAP, HDBSCAN gave up on half the questions.

> Interview line: "I didn't just run UMAP and HDBSCAN; I scored them against labelled intents,
> against k-means given the answer, and against HDBSCAN without UMAP, and reported the noise share."

Check yourself: why does HDBSCAN struggle in 1024 dimensions, and what does UMAP change?

## 2. When will messages arrive?

Twelve weeks of hourly counts. The baseline is "next week looks like last week" (seasonal
naive); the method used spreads a smoothed weekly total over the hour-of-week profile, shrunk
towards weekday, Saturday and Sunday averages. A rolling-origin backtest scores it: MASE 0.86
means 14% less error than the baseline. The useful sentence for an owner is "two thirds of next
week's messages will arrive while the office is closed".

> Interview line: "Every forecast I show comes with the naive baseline it has to beat and the
> coverage of its intervals, measured on weeks it didn't see."

Check yourself: what does a MASE above 1 tell you, and why is seasonal naive a fair baseline here?

## 3. Drift that does not cry wolf

PSI measures how far this week's mix of intents moved from the last four weeks. The first
version, written the textbook way, fired on pure noise 92% of weeks at 50 messages a week,
because a rare intent that happens not to appear blows the score up. The fix: merge intents
under 5% into "other", smooth the counts, and require both a large PSI and a chi-square p-value
under 0.01. Simulated false alarms fell to about 2% of weeks, and it catches most severe
load-shedding weeks from 50 messages a week. A weekly Lambda runs it per tenant and DET-12 alarms on it.

> Interview line: "My drift alarm was useless at small-business volume until I measured its
> false-alarm rate; now it needs effect size and significance, and I can tell you both rates."

Check yourself: why does an effect-size threshold alone fail at small n, and a p-value alone fail at large n?

## 4. A/B testing a prompt, honestly

Customers are assigned to a prompt by hashing the experiment id with their number, so each one
sees one arm for the whole experiment with no assignment table. The hypothesis, metric, minimum
effect, alpha, power and sample size are written down before any data (`experiments/`), and
the hash of that file is stored when the experiment starts, so it cannot quietly change. The
tool refuses to analyse early: in simulated A/A tests, peeking ten times found a false winner
19% of the time instead of 5%. And the plan says the uncomfortable part out loud: one small
business needs about a year to detect a 5-point improvement, so prompts are tested offline first.

> Interview line: "The most useful output of my A/B tooling was a sample-size calculation that
> said a single SME can't run this test in a reasonable time, and what to do instead."

Check yourself: why does assigning by customer rather than by message matter for the analysis?

## 5. A restore is finished when it is safe, not when the rows are back

`restore_check` proves on the restored copy what CI proves on a fresh database: every
migration present and unedited, forced RLS everywhere, the append-only triggers on, the app
role unable to bypass RLS, no rows visible without a tenant, and how much data was lost. The
local drill backs up with `pg_dump`, restores into a new database, runs the check, then breaks
the copy on purpose to prove the check fails when it should. The AWS script does the same with
Aurora point-in-time restore and records the real recovery time.

> Interview line: "My restore drill breaks the restored copy on purpose, because a check that
> can't fail proves nothing."

Check yourself: name two protections a careless restore could lose while every row comes back.

## 6. The incident tabletop found three real gaps

Walking through a phished staff session showed that Cognito cannot recall a token already
issued. The fix uses something already true: every inbox request checks that the user is an
active staff row, so `admin deactivate-staff` refuses the next request even with a valid token.
The walk-through also added "what did this account read" (`audit.reads_by`), the breach notice
template the incident plan pointed to but did not have, and a per-tenant switch to pause the
assistant without a deploy.

> Interview line: "The tabletop's most valuable output was code: an immediate cut-off that
> doesn't depend on token expiry, with a test using the same unexpired token."

Check yourself: under POPIA, who notifies the Information Regulator, the operator or the responsible party?

## 7. Delivery metrics and the Well-Architected review

DORA's four keys (deployment frequency, lead time, change failure rate, time to restore) come
from this repository's own deploy runs every Monday; prod runs carry the released ref in their
name so lead time starts at the commits actually shipped. The Well-Architected review walks
all six pillars and the Generative AI Lens: no high-risk issue is open, and every medium one is
either fixed or accepted with a reason and a date to revisit.

> Interview line: "My Well-Architected review has no open high-risk issues, and each accepted
> medium one is written down as a risk with a trigger to revisit, not hidden."

Check yourself: which DORA key would a canary rollback show up in?
