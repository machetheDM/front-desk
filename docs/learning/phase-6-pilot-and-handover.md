# Phase 6: pilot and handover, in plain language

What was run: the full onboarding replay for a made-up non-demo client on a throwaway local
database (create tenant, load documents, add staff, replay a WhatsApp export), the conformance
kit against its reference server, the offboarding export and erase, the ROI sheet, and a new
test that parses every admin command written in the docs. What was not run: a real client.
That is the one step this phase cannot do alone.

## 1. The sales motion is a replay, not a demo

The discovery workshop leaves with last month's chats and the client's documents. The replay
runs on a laptop with the offline pipeline: no AWS, no model, personal details removed, nothing
sent to anyone. The owner sees their own questions answered or handed to their own staff, with
honest intervals, and a list of what their documents are missing. The export is deleted the same day.

> Interview line: "My first deliverable to a client is their own last month replayed offline,
> before any contract, with the gaps in their documents listed."

Check yourself: why run the replay with the offline pipeline even when Bedrock is available?

## 2. An integration the client builds, and a kit that checks it

The order-status contract is one endpoint: signed requests only, replays refused, the order
told only to the number on it, and the same "not found" whether the order does not exist or
belongs to someone else, so the API cannot be used to test which orders exist. The client's
developer runs the conformance kit against their sandbox; it has eight checks, and it is itself
tested: it passes a thirty-line reference server, and fails that server when it is changed to tell
anyone the order, and when the secret is wrong. If their
system is down, the customer is told so and handed to a person.

> Interview line: "I don't just document an integration contract; I ship a conformance kit the
> client runs, and the kit is tested against a deliberately broken server to prove it catches the failure."

Check yourself: why must "unknown order" and "someone else's order" give identical answers?

## 3. ROI with the low case in plain sight

Staff time saved rests on a measured volume (with its interval); extra sales rest on stated
assumptions with their sources, and the low case assumes none. In the demo, the low case loses
R851 a month, and the sheet says so in bold. The pilot's job is to replace the sales assumption
with the client's own records.

> Interview line: "My ROI sheet says when the pessimistic case doesn't pay, because a client who
> finds that out later doesn't renew."

Check yourself: which ROI line would you trust more, and why?

## 4. Ending well: export, erase, certificate

The client is the responsible party and owns the data. Offboarding exports everything with a
hashed manifest, refuses to erase until the export's hash is quoted and the assistant is
paused, erases every personal field, and writes a certificate that also says what remains:
ids and timings (never text), consent records as proof of lawful messaging, and backups until
they expire. Another tenant's data is checked untouched in the test.

> Interview line: "The erase can't run before the export exists; it checks the export's hash in
> the audit log, and the certificate lists what remains and why."

Check yourself: why does the erase require the assistant to be paused first?

## 5. The docs are tested like code

Two real bugs were found this phase by treating documentation as executable. A test now
parses every `python -m frontdesk.admin` line in the docs; its first run found that *every*
documented command failed ("unrecognized arguments: --profile") and six examples were missing a
required option. A CI step parses every PowerShell block with PowerShell's own parser; its first
run found `<you>@gmail.com` placeholders, which are syntax errors in PowerShell.

> Interview line: "The owner pastes my commands, so my CI parses them. That found a bug that
> would have failed on the first command of the deploy guide."

Check yourself: what kinds of documentation errors can a parser catch, and what can it not?

## 6. Documents go stale

A correct citation of last year's price list is still a wrong answer. Every document now carries
a review date (six months if none is given), and the insights screen lists the ones that are due.

> Interview line: "Grounding isn't enough if the ground moves, so every document has a review
> date and the owner's screen shows when it's due."

Check yourself: which OWASP LLM risk does the review date address?
