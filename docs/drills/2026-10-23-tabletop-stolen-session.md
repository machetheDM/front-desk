# Tabletop: a staff member's inbox session is stolen

Date: 23 October 2026. Type: tabletop (walked through on paper against the real tooling; the
containment and scoping steps were then built and tested because the walk-through found them
missing). Severity in play: P1 (`docs/INCIDENT-PLAN.md`). Players: incident lead (Machethe DM);
the client contact and their Information Officer are played by the same person for the drill.

## Scenario

Bushveld's sales person, Pieter, clicks a link in a fake "Microsoft 365" e-mail on a Sunday
evening and types his inbox password and TOTP code into a look-alike page. The attacker relays
both to Cognito within the 30-second TOTP window and holds a valid session: an ID token good
for 60 minutes and a refresh token good for 12 hours (`app-stack.ts`, StaffPool client). This is
accepted risk R-03 happening: TOTP is not phishing-resistant.

## Timeline and decisions

| Time | Inject | Decision and action | Evidence it works |
|---|---|---|---|
| 21:14 | DET-05 e-mail: one staff account read more than 40 conversations in an hour (`READ_ALERT_THRESHOLD`) | Open the incident log. Read the event: actor is Pieter's staff id, 64 reads | `test_inbox_api.py::test_unusual_read_volume_raises_an_event` |
| 21:18 | Pieter does not answer his phone | Treat as P1: personal information likely accessed by someone unauthorised | INCIDENT-PLAN section 2 |
| 21:20 | **Gap found:** the runbook said "disable the Cognito user", but Cognito cannot recall a token already issued, and API Gateway accepts any unexpired token for up to 60 minutes | Use the check every inbox request already makes (the token's user must be an *active* staff row): `admin deactivate-staff` sets the row inactive, so the very next request is refused, then signs the user out of Cognito and disables them | Built today: `staff.deactivate_plan`, `test_incident_tools.py::test_cutting_off_a_staff_member_refuses_their_next_request_even_with_a_valid_token` |
| 21:25 | Is the assistant itself affected? | No: the assistant only answers from documents and never shows one customer's details to another. Do not pause it. (If it were, `admin pause-assistant`.) | `test_controls.py` |
| 21:30 | Preserve | Note CloudTrail event ids for the user pool's sign-in calls; take a cluster snapshot (`aws rds create-db-cluster-snapshot`); the audit log is append-only even for its owner, so it needs no copy to stay intact | `test_audit.py::test_the_audit_log_rejects_tampering_even_by_the_table_owner` |
| 21:35 | **Gap found:** "which conversations did the attacker read?" The audit log answered "who read this conversation", not "what did this account read" | `audit.reads_by(actor, since, until)` lists every read by the account in the window: 64 conversations, 61 distinct customers | Built today: `test_incident_tools.py::test_what_an_account_read_can_be_listed_for_the_incident_window` |
| 21:40 | Did it cross tenants? | No: the token carries Bushveld's tenant id, and the database refuses other tenants' rows below the app (forced RLS). State it in the notice with the evidence | `test_tenant_isolation.py`, `test_inbox_api.py::test_a_token_for_another_tenant_cannot_see_this_tenants_conversation` |
| 21:55 | Tell the client (we are the operator: POPIA s21 says immediately) | **Gap found:** the incident plan pointed to a notice template that did not exist | Written today: `docs/engagement/breach-notice-template.md` (s21 notice with the s22(5) contents the client needs) |
| next day | The client's Information Officer decides on s22 notification to the Regulator and customers | We supply the filled template and an update when Pieter's account is restored with a new password and a new TOTP secret | INCIDENT-PLAN section 4 |

## What would have caught it sooner

- **Phishing-resistant MFA** (passkeys or FIDO2 keys) stops this attack outright; it needs Cognito
  Essentials or Plus. Stays accepted risk R-03 until the first paying client, now with this
  scenario written down as the reason to revisit it.
- A read alert at 40 an hour caught 64 reads in 40 minutes. A lower threshold would fire on a
  busy Monday morning; keep 40 and add a second signal later (a sign-in from a new country).

## Actions

| # | Action | Status |
|---|---|---|
| 1 | Immediate staff cut-off that does not depend on token expiry | done (`admin deactivate-staff`) |
| 2 | "What did this account read" query for scoping | done (`audit.reads_by`) |
| 3 | Breach notice template for the operator-to-client notice | done |
| 4 | Incident plan updated with the real commands | done (`docs/INCIDENT-PLAN.md` step 2) |
| 5 | Phishing-resistant MFA for staff | open: R-03, first paying client |

## Interview line

"I ran a tabletop for a phished staff session and it found three gaps in my own runbook. The
best one: Cognito can't recall a stolen token, but every request already checks the staff row
in the database, so deactivating that row cuts the attacker off on the next request. There's a
test that proves it with the same unexpired token."
