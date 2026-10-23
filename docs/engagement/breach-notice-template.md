# Security compromise notice (template)

Who sends what, under POPIA:

- **Front Desk (the operator) to the client (the responsible party): immediately** once there
  are reasonable grounds to believe personal information was accessed or acquired by an
  unauthorised person (section 21(2)). This template is that notice.
- **The client to the Information Regulator and to the affected customers: as soon as
  reasonably possible** (section 22). The client decides; we supply everything below so they
  can. The Regulator has a prescribed form for this; check the Regulator's website for the
  current way to submit it before relying on any route written here.

Fill every bracket. Write "not yet known" rather than guess; send an update when it is known.
Keep the sent copy in the private incident log, never in this public repository.

---

**To:** [client contact name, role] and [client's Information Officer]
**From:** Machethe DM, Front Desk (operator)
**Sent:** [date and time, SAST]
**Incident reference:** [INC-YYYY-MM-DD-short-name]
**Status:** [initial notice / update n / final]

## 1. What happened

[One paragraph in plain language. Example: "At 21:14 on 3 November, our monitoring alerted us
that one staff inbox account read 64 customer conversations in 40 minutes, far above normal.
We believe the account's sign-in was captured by a phishing message."]

- When it started (first unauthorised activity): [date time] ([how we know: audit log, CloudTrail])
- When it was detected, and how: [date time], [detection id, e.g. DET-05]
- When it was contained: [date time], [what was done]

## 2. What personal information is involved

| Information | Involved? | Detail |
|---|---|---|
| Customer names as given | [yes/no] | |
| Phone numbers (WhatsApp) | [yes/no] | |
| Message contents | [yes/no] | [e.g. questions about prices and installation dates] |
| ID numbers, bank or card details | [yes/no] | [redaction removes these before the assistant; they can still be in the raw message staff see] |
| Company documents (prices, policies) | [yes/no] | |

- Number of customers (data subjects) affected: [n], from `audit.reads_by` for the account and window
- Other tenants affected: [none; tenant isolation is enforced in the database (RLS) / details]

## 3. Possible consequences for the customers (s22(5)(a))

[Example: "The person could contact your customers pretending to be your business, using
details from their conversations, for example to ask for a payment."]

## 4. What we have done and will do (s22(5)(b))

- [time] [Staff account deactivated: the next request with any stolen token was refused]
- [time] [Cognito global sign-out and account disabled]
- [time] [Audit log and CloudTrail records preserved; database snapshot taken]
- [planned] [e.g. move staff sign-in to phishing-resistant MFA (accepted risk R-03)]

## 5. What your customers can do (s22(5)(c))

[Example: "Treat any message asking for payment or personal details that does not come from
your official number with suspicion; your business will never ask for an ID number or bank PIN
on WhatsApp; call the office on [number] if unsure."]

## 6. Who did it (s22(5)(d))

[Identity of the unauthorised person, if known; otherwise "not known".]

## 7. Contact

[Name, phone, e-mail] for questions. Next update by [date time].
