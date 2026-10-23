# Discovery workshop (90 minutes, at the client)

Purpose: leave with everything needed to run the replay on the client's real questions, and to
decide together whether a 30-day pilot is worth it. Bring a printed copy of this page and the
demo replay report (`docs/evidence/replay-demo-offline/owner-report.md`).

Who: the owner, the person who answers WhatsApp today, and whoever handles sales or bookings.

## 0. Before the meeting (by e-mail)

- Ask for last month's WhatsApp chats, exported from the business phone (WhatsApp, the chat,
  More, Export chat, Without media), or a spreadsheet of questions with dates. Say plainly that
  personal details are removed before anything is processed, nothing is sent to any customer,
  and the export is deleted after the replay.
- Ask for whatever they already have in writing: price list, warranty terms, FAQ, opening hours,
  service areas, booking rules.

## 1. How customers reach them today (15 min)

| Question | Their answer |
|---|---|
| Which channels (WhatsApp, website, phone, Facebook)? Rough messages per week on each? | |
| When do messages arrive? How many after hours or on weekends? | |
| Who answers, and how fast in office hours? After hours? | |
| What happens to a message nobody answers? | |
| Which languages do customers write in (English, Sepedi, Afrikaans, other)? | |

## 2. What customers ask (20 min)

Go through ten real chats together. For each question, mark: **A** could be answered from a
document they have; **D** needs a document that does not exist yet; **P** needs a person
(price negotiation, complaints, faults, refunds, bookings, anything binding).

- Top five questions by volume:
- Questions that must **always** go to a person, and to whom:
- Things the assistant must **never** say (competitor names, promised dates, discounts):

## 3. People and routing (10 min)

| Person | Role | Handles (prices, bookings, faults, complaints, accounts) | Office hours |
|---|---|---|---|
| | owner | | |
| | | | |

Every handoff names one of these people. Nobody is on the list who does not want to be.

## 4. Documents (15 min)

| Document | Exists? | Owner | How often it changes | Review by |
|---|---|---|---|---|
| Price list | | | | |
| Warranty and returns | | | | |
| Opening hours and areas served | | | | |
| Booking and payment rules | | | | |
| FAQ | | | | |

Each document gets a review date; the insights screen lists those that are due, because an
out-of-date price quoted exactly is still a wrong answer.

## 5. Systems (10 min)

- Where do orders or jobs live (spreadsheet, accounting package, job system)? Does it have an
  API, and who could build one endpoint (`docs/engagement/integration-contract.md`)?
- Who would administer staff logins (two-factor authentication is required)?

## 6. POPIA and consent (10 min)

- Who is their **Information Officer** (registered with the Information Regulator)?
- Read the assistant's first message to them: it says it is an automated assistant and that a
  person is available any time. Agree the wording.
- Marketing: only to customers who opt in separately (JOIN UPDATES); being answered is not
  consent to marketing (POPIA s69). Do they want marketing at all?
- Explain where data lives: Cape Town; only redacted text goes to Frankfurt for the language
  model (`docs/POPIA-S72.md`). Front Desk is their operator; they remain the responsible party.

## 7. What success would look like (10 min)

Agree the numbers the pilot will be judged on, and today's baseline for each if they know it:

| Measure | Today (their estimate) | Pilot target |
|---|---|---|
| After-hours messages answered within a minute | | |
| Routine questions staff no longer answer by hand, per week | | |
| Time from first message to a named person, for handoffs | | |
| Quotes or sales from WhatsApp leads (from their records) | | |

## After the workshop (within a week)

1. Run the replay on their export locally (`docs/engagement/onboarding-checklist.md`, part A).
2. Send the owner report and the ROI sheet (`python -m frontdesk_ml.roi`), with the low case
   shown as clearly as the high one.
3. If they want to go ahead: the pilot charter (`docs/engagement/pilot-charter.md`).
4. Delete the export and the local replay database.
