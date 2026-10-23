# Deploy readiness, in plain language

What was run: the new `python -m frontdesk.deploy_check` against a faked account (botocore's
Stubber), its tests, the Data API tests with a cluster that pretends to be paused, and the full
suite. What was not run: the checker against a real account. That happens when you start
DEPLOY.md at step 5.

## 1. A checker for the deploy guide

Every step in DEPLOY.md ends with "you should see". The checker turns those sentences into read-only AWS
calls and prints one line per check: PASS, FAIL (done but wrong), TODO (not done yet) or SKIP
(no profile for that account yet). The last line names the first step that needs you. It catches
the mistakes that are easy to make and hard to see: root without MFA, an SCP attached to the
wrong place, a profile that points at the wrong account, an alert e-mail never confirmed (so
alarms go nowhere), a stack whose last deploy rolled back.

> Interview line: "My deploy guide is executable: a read-only checker verifies each step against
> the real account and tells you which step to redo."

Check yourself: why is "TODO" a different result from "FAIL"?

## 2. Testing AWS code without AWS

The tests fake AWS with botocore's Stubber, which checks every canned response against AWS's
own service definitions. If the checker read a field that doesn't exist, or expected the wrong
shape, the test would fail here and not on your laptop. Another test reads the CDK source to
make sure the checker uses the same account, policy and stack names.

> Interview line: "I test AWS integrations with Stubber, so the fake responses are validated
> against the real API models, and a test keeps the checker's names in step with the CDK app."

Check yourself: what would a plain mock (any object with the right method names) fail to catch?

## 3. A bug found by checking the guide against AWS's rules

Step 8 turns on Cape Town in the new accounts from the management account. AWS only allows that
when Organizations has trusted access for Account Management, and step 6 never turned it on.
Step 8 would have been refused. Step 6 now enables it, and the checker checks it.

> Interview line: "Writing the checker made me re-read AWS's rules for each call, and it found a
> missing prerequisite that would have stopped the deploy at step 8."

Check yourself: which account can call `enable-region` for a member account, and what must be on first?

## 4. A database that sleeps must be woken gently

Aurora at zero capacity pauses when nobody uses it. The first call after that fails with
"resuming" for about 15 seconds. Without handling it, the first customer of the morning gets an
error. The Data API client now waits 1, 2, 4, 8 and 8 seconds on that one error, 23 seconds
in all, inside the 29 seconds API Gateway allows. Every other error still fails at once.

> Interview line: "Scale-to-zero has a cold-start cost. I made the client wait for exactly that
> error, with a ceiling under the gateway timeout, and tested both the wait and the ceiling."

Check yourself: why retry only `DatabaseResumingException` and not every error?
