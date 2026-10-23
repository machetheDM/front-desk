# Phase 2: web chat and the staff inbox, in plain language

## 1. Three steps, each a transaction

A message goes through `receive` (store it, record consent, check rate limits), `answer` (run
the agent, store the outcome) and, if the language layer fails, `degrade` (hand over and tell
the customer a person will reply). For WhatsApp these are Step Functions states; for web chat
one Lambda runs them in a row. The Step Functions payload carries only ids. The customer's
words stay in the database and are read inside each task, so they never appear in execution
history.

> Interview line: "Step Functions orchestrates the pipeline with a Catch that turns any model
> failure into a handoff, and its payload carries only ids, so customer text never lands in
> execution history or logs."

Check yourself: why does a WhatsApp retry with the same message id not produce a second answer?

## 2. Who is calling? Tenant from the token, never from the request

Staff sign in with Cognito (MFA required). API Gateway verifies the ID token before our code
runs. The tenant is the `custom:tenant_id` claim, which cannot be changed after the user is
created, and the role is the Cognito group. Our code then checks, inside the tenant's own
transaction, that this person is an active staff member. Remove them from the staff table and
their still-valid token stops working on the next request.

> Interview line: "Authorization derives the tenant from an immutable, verified token claim and
> re-checks active staff membership under RLS on every call, so there is no tenant id in any
> request for an attacker to change."

Check yourself: what would go wrong if the inbox took the tenant id from a query parameter?

## 3. Reads are audited, and bulk reading is a detection

Opening a conversation writes an audit row in the same transaction as the read: if the audit
write fails, the read fails. "Who read Lerato's conversation, and when?" is one query. After
each read we count how many conversations this staff member opened in the last hour; past the
threshold the app raises a `UnusualTranscriptReads` event, which EventBridge turns into DET-05.

> Interview line: "I audit reads, not just writes, transactionally, and turn read volume into a
> detection with a runbook, which is how a stolen staff session or an insider shows up."

Check yourself: why is the alert raised when the count reaches threshold + 1 rather than on every read above it?

## 4. The inbox is a static site with a strict CSP

The inbox is plain HTML and JavaScript files on S3 behind CloudFront: no server to patch. The
Content-Security-Policy allows scripts only from our own domain. Next.js's newer router and
bundler add inline scripts that this policy blocks, so we use the Pages Router with webpack,
and a build step fails if an inline script ever appears. Sign-in is the OAuth code flow with
PKCE (a one-time secret the browser makes and proves later), and the token lives only in
memory.

> Interview line: "I kept the CSP at script-src 'self' and changed the build to fit it, with a
> check that fails the build on any inline script, instead of weakening the policy."

Check yourself: what does PKCE protect against when the app cannot keep a client secret?

## 5. Streaming without streaming risk (ADR 0009)

Every safety check runs on the complete answer. So the web chat streams *progress* ("Checking
our information...") at once, then sends the *validated* answer in sentence-sized parts over a
WebSocket. It never streams raw model tokens, because a token cannot be taken back once shown.

> Interview line: "I stream status and the validated answer, not raw tokens, because grounding
> and commitment checks need the whole answer and a streamed token can't be unsent."

Check yourself: what would a customer have seen with token streaming if the model started "Yes, 20% off..."?

## 6. POPIA deletion that reaches everything

A deletion request (owner only, preview first, then confirm) overwrites the customer's message
text, removes knowledge chunks and their embeddings made from that customer's conversations,
anonymises the customer record, and is itself audited. The event stream and audit log stay:
they contain ids and decisions, never text, so the business keeps its statistics without
keeping personal information.

> Interview line: "Deletion reaches transcripts, derived chunks and embeddings; the event stream
> survives because a database constraint guarantees it never contained text."

Check yourself: which table would leak personal information after deletion if event payloads were allowed to contain message text?
