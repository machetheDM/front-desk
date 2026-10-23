# Drill: the model is down, the front desk stays open

Date: 23 October 2026. Type: failure-injection drill (game day), demo tenant only.
Rule under test: "Model down means the business still runs" (`AGENTS.md`), threat D-02 and
ATLAS AML.T0029 Denial of AI Service (`docs/AI-SECURITY.md`).

## Why this drill

Bedrock runs in another country (ADR 0002). Throttling, a regional incident or a quota
mistake will happen. The client must never find out from a customer that "the WhatsApp bot
ignored me". What has to stay true while the model is down:

1. Every customer gets a reply that says a person will answer, and when.
2. Every affected conversation lands in the staff inbox, assigned to a named person, with a summary.
3. Rules that need no model keep working: "talk to a person", STOP, duplicate suppression.
4. A crash the agent did not anticipate (a timeout) is caught by the workflow, not lost.
5. The owner's dashboard still works, because it is computed from events.
6. Operations is told (DET-06) without a customer having to complain.
7. Recovery needs nobody to reset anything.

## Run 1: local, every pull request (done)

`backend/frontdesk/drills.py` injects two failures into the real pipeline code: a Bedrock
`ThrottlingException` (raised as `ModelUnavailable`, so the agent's `DEGRADED` path runs) and a
`RuntimeError` timeout (so only the Step Functions `Catch -> Degrade` branch can save the
message). It then drives the staff inbox API and the owner insights exactly as the Lambdas do.

The recorded run is [`2026-10-23-model-down.run.md`](2026-10-23-model-down.run.md): **10 of 10
steps passed**. `backend/tests/test_drills.py` runs the same drill on every pull request, so this
record cannot quietly stop being true.

To repeat it on your laptop (PowerShell, Docker Desktop running):

```powershell
docker compose up -d db
docker compose exec db psql -U postgres -c "CREATE DATABASE frontdesk"
python -m frontdesk.db.migrate --dsn postgresql://postgres:local-only@localhost:5433/frontdesk
docker compose exec db psql -U postgres -d frontdesk -c "ALTER ROLE frontdesk_app LOGIN PASSWORD 'local-app'; GRANT CONNECT ON DATABASE frontdesk TO frontdesk_app"
python -m frontdesk.demo_tenant --dsn postgresql://frontdesk_app:local-app@localhost:5433/frontdesk --write
python -m frontdesk.drills model-down --dsn postgresql://frontdesk_app:local-app@localhost:5433/frontdesk --record docs/drills
```

`psql` runs inside the database container, so nothing extra is installed on Windows. If the
second line says the database already exists, carry on. You should see a table ending in
`Result: 10 of 10 steps passed` and the command exits with 0.

## Run 2: AWS dev, with a real Bedrock failure (to do after DEPLOY.md step 17)

SEAM(drill-model-down-aws): needs the dev account. The failure is real, not simulated: an
inline IAM deny on the role of every Lambda that calls Bedrock (the WhatsApp answer step, the
web chat and the web chat WebSocket handler each have their own role) makes every Bedrock call
fail with `AccessDeniedException`, which the code turns into degraded mode. Nothing in the
code or the deployed version changes, so the drill tests what production runs.

```powershell
$fns = "frontdesk-dev-answer", "frontdesk-dev-web-chat", "frontdesk-dev-web-chat-ws"
$roles = $fns | ForEach-Object { (aws lambda get-function-configuration --function-name $_ --profile fd-dev --query Role --output text).Split('/')[-1] }
'{"Version":"2012-10-17","Statement":[{"Sid":"DrillModelDown","Effect":"Deny","Action":"bedrock:*","Resource":"*"}]}' | Out-File -Encoding ascii drill-deny.json
$roles | ForEach-Object { aws iam put-role-policy --role-name $_ --policy-name drill-model-down --policy-document file://drill-deny.json --profile fd-dev }
$start = Get-Date
```

Then, within five minutes, send three messages from the dev web chat (the CloudFront URL from
DEPLOY.md step 16): "How much is the Home 5kW backup?", "Do you install in Tzaneen?",
"I want to talk to a person". What you should see:

| Check | Expected |
|---|---|
| Web chat replies | The first two: "Thanks for your message. A person from Bushveld Solar & Electrical will reply..."; the third: a normal handoff naming Naledi |
| Staff inbox | Three conversations in the queue, each with a summary |
| Email from the dev alarm topic | `frontdesk-dev-degraded-mode` in ALARM (DET-06), within about 10 minutes |
| Email from the security topic | DET-02 "IAM change outside the deploy pipeline" three times, naming your own Identity Center role: expected, and proof DET-02 works |

```powershell
aws cloudwatch describe-alarms --alarm-names frontdesk-dev-degraded-mode --profile fd-dev --query "MetricAlarms[0].StateValue" --output text
# Recover: remove the deny, then send one more question; it must be answered with a citation.
$roles | ForEach-Object { aws iam delete-role-policy --role-name $_ --policy-name drill-model-down --profile fd-dev }
Remove-Item drill-deny.json
"Drill lasted {0:N0} minutes" -f ((Get-Date) - $start).TotalMinutes
```

Record what you saw in a copy of this table, with the times, as
`docs/drills/<date>-model-down-aws.md`, and add the link to `docs/DEPTH.md`.

## What the drill taught

- **The Catch branch is the real net.** The agent only degrades on errors it recognises.
  A timeout bypasses it entirely; the Step Functions `Catch` with `States.ALL` is why step 5
  passes. Keep the Catch even if the agent gets "smarter".
- **Rules first pays off twice.** STOP, "talk to a person" and duplicate suppression never
  needed the model, so they kept working: that is design, not luck.
- **The degraded reply cannot promise a time it does not know.** The agent's degraded reply
  uses office hours; the Catch's reply says "as soon as possible", because a crashed step may
  not have the tenant's hours loaded. Both name the business and never pretend to be a person.
- **Staff load is the cost.** Every degraded message is a handoff. DET-06's runbook tells the
  owner to expect more inbox traffic; the threshold (3 in 5 minutes) is set so one flaky call
  does not page anyone.

## Interview line

"I don't claim the system survives a model outage. I break it on purpose on every pull request,
ten checks from the customer's reply to the owner's dashboard, and there's a version that
denies Bedrock in the real AWS account and watches two alarms fire."
