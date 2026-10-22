# Incident plan

Who does what when something goes wrong. Short on purpose: in an incident nobody reads a
long document. Alerts and their first steps are in `docs/DETECTIONS.md`.

## 1. Roles (pilot scale)

| Role | Who | Responsibility |
|---|---|---|
| Incident lead | Machethe DM | Decides, keeps the log, talks to the client |
| Client contact | Named in the pilot charter (`docs/engagement/pilot-charter.md`) | Decides what their customers are told |
| Information Officer (POPIA) | The client's (each client is the responsible party; we are the operator) | Decides on s22 notification to the Regulator and data subjects |

## 2. Severity

- **P1**: customer data exposed or likely exposed; tenant boundary broken; root or CI credentials misused; assistant making commitments at scale. Act within 15 minutes, any hour.
- **P2**: a control failed without known exposure (detection fired, guardrail bypass found in testing, degraded mode for more than 30 minutes). Same day.
- **P3**: everything else. Next working day.

## 3. The first hour (P1)

1. **Start the log** (`docs/incidents/YYYY-MM-DD-short-name.md` in a private copy, not this public repo): time, what you saw, what you did. Every action gets a line.
2. **Contain without destroying evidence.** Prefer disabling over deleting: disable the Cognito user, detach the policy, switch the channel to "a person will reply" (`FRONTDESK_FORCE_DEGRADED=true` in SSM for the tenant), revoke sessions. Do not delete log groups, buckets or stacks.
3. **Preserve**: note CloudTrail event ids, export the relevant audit_log rows (an audited export), snapshot the Aurora cluster (`aws rds create-db-cluster-snapshot`).
4. **Assess scope**: which tenants, which customers, which data, since when. `audit.who_read` and CloudTrail answer most of this.
5. **Tell the client** within the hour for P1, with what is known and unknown.

## 4. POPIA section 22 (security compromises)

We are the client's **operator**. Section 21 requires an operator to notify the responsible
party *immediately* where there are reasonable grounds to believe personal information has been
accessed or acquired by an unauthorised person. The responsible party (the client) notifies the
Information Regulator and the affected data subjects as soon as reasonably possible. We
provide them: what happened, what information, how many data subjects, what we have done, what
they and their customers can do. Template: `docs/engagement/breach-notice-template.md`.

## 5. After the incident

Within five working days: a blameless write-up (timeline, root cause, what detected it, what
would have detected it sooner), at least one new test or detection, and a threat-model update.
Drills count: the Phase 4 "model down" drill and the Phase 5 restore and incident drills are
recorded the same way in `docs/drills/`.

## 6. Break-glass

The management account root user (hardware MFA) and the `FrontDeskBreakGlass` role are the only
principals exempt from the protective SCPs. Use only when Identity Center is unavailable or an
SCP blocks incident response. Announce in the log first; DET-01 will fire, which is expected.
After use: rotate the root password and review every action taken.
