# Pilot charter (template)

Fill every bracket with the client, sign it, and keep the signed copy privately (never in this
public repository). One page on purpose: anything not here is not promised.

**Client (responsible party):** [business name], [registration number]
**Client contact:** [name, role, phone]  **Information Officer:** [name]
**Operator:** Machethe DM, Front Desk  **Dates:** [start] to [start + 30 days]

## 1. What the pilot is

An automated assistant answers customer questions on [WhatsApp number / website] from the
documents listed in section 4, and hands everything else to the named staff in section 3. The
staff inbox shows every conversation, the handoff queue and the insights screen.

## 2. What it will never do

These are built in and tested, not settings:

- Pretend to be a person. The first message says it is an automated assistant; "PERSON" always reaches a person.
- Answer without a source. No source in the approved documents means a handoff, never a guess.
- Make a commitment: no discount, refund, credit, booking date or price change.
- Market to anyone who has not opted in separately (POPIA s69).
- Guess anyone's title or pronoun.
- Use this business's data to train anything, or share it with another client.

## 3. People and routing

| Person | Handles | Inbox login (with two-factor authentication) |
|---|---|---|
| [name] | [e.g. pricing, discount, general] | [e-mail] |
| [name] | [e.g. warranty, fault, installation] | [e-mail] |
| [name] | [e.g. payment, booking, order_status] | [e-mail] |

## 4. Documents (approved by the client before go-live)

| Document | Approved by | Review by |
|---|---|---|
| [Price list] | [name] | [date] |
| [Warranty] | [name] | [date] |

## 5. How we will judge it (measured from the conversation events, with 95% intervals)

| Measure | Target | Source |
|---|---|---|
| After-hours messages answered at once | at least [50]% | insights screen |
| Handoffs that name a person | 100% | event stream |
| Answers that cite an approved document | 100% of answers sent | event stream; validator |
| Commitments made by the assistant | 0 | red-team suite and validator |
| Quotes or sales from WhatsApp leads, against the 30 days before | reported, not promised | the client's own records |

At day 30 the client decides: continue, change, or stop. No automatic renewal.

## 6. Data and POPIA

- Front Desk acts as the client's **operator** (POPIA s21): it processes personal information
  only to run this service, keeps it secure (s19: isolation in the database, encryption, audited
  reads, two-factor sign-in), and tells the client **immediately** if there are grounds to
  believe it was accessed without authority (template: `docs/engagement/breach-notice-template.md`).
- Data is stored in Cape Town. Only text with personal details removed goes to Frankfurt for the
  language model (`docs/POPIA-S72.md`).
- A customer asking for deletion is handled from the inbox; it reaches messages, derived chunks
  and embeddings.
- **At the end:** the client receives a full export with a hashed manifest, then everything
  personal is erased and the client receives a certificate listing what was erased and what
  remains (ids and timings only; backups expire within 14 days). Command: `admin offboard-export`,
  then `admin offboard-erase`.

## 7. If something goes wrong

- The operator can pause the assistant at once; customers are then told a person will reply,
  and STOP keeps working.
- Incidents follow `docs/INCIDENT-PLAN.md`; a P1 is reported to the client contact within the hour.
- Either side can stop the pilot at any time by e-mail. Stopping triggers the export and erasure in section 6.

## 8. Money

Pilot fee: [R0 / amount]. AWS and WhatsApp costs for this business, estimated at
[R amount] a month (`docs/engagement/roi-[client].md`), are [included / passed through at cost].

Signed for the client: ____________________ Date: ________
Signed for Front Desk: ____________________ Date: ________
