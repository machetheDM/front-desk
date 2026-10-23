# Drill: back up, restore, and prove the restore is safe

Date: 23 October 2026. Type: restore drill. Targets for the pilot: **RPO 5 minutes** (data we
can afford to lose) and **RTO 2 hours** (time until a restored database is serving safely).

## Why "safe", not just "restored"

A restore that brings the rows back but not the protections is worse than no restore: the app
would serve every tenant's data with nothing in the database to stop a bug from crossing the
boundary. So a restore is finished only when `python -m frontdesk.restore_check` passes on the
copy. It checks, read-only:

1. every packaged migration is recorded with the same SHA-256 (nothing missing, nothing edited);
2. every app table has forced RLS, a policy and a NOT NULL tenant_id;
3. the append-only triggers exist and are enabled (audit log, consents, event stream);
4. the app role cannot bypass RLS and owns nothing;
5. as the app role with no tenant set, every table reads as empty;
6. per tenant, the data is there, and the newest event against the restore point gives the
   data actually lost (the measured RPO).

## How backups work in AWS

Aurora keeps continuous backups (point-in-time restore to any second in the window): 7 days in
dev, 14 in prod (`data-stack.ts`), encrypted with the data key. The latest restorable time is
normally within five minutes of now, which is where the 5-minute RPO comes from. Prod clusters
have deletion protection and snapshot on stack removal.

## Run 1: local logical backup and restore (done, runs on every pull request)

`python -m frontdesk.drills backup-restore` dumps the database with `pg_dump`, restores it into
a new database with `pg_restore`, runs the restore check on the copy, then **breaks the copy on
purpose** (removes forced RLS from `app.messages`, disables the audit log's append-only
trigger) and confirms the check fails and names both. That last step is what shows the check is
not just printing "pass". Record: [`2026-10-23-backup-restore.run.md`](2026-10-23-backup-restore.run.md),
4 of 4 steps. `backend/tests/test_restore.py` runs it in CI, and CI is set to fail, not skip,
if the PostgreSQL client tools are missing.

CI runs this drill on every pull request, so running it on your laptop is optional. It needs
the PostgreSQL 16 command-line tools (`pg_dump`, `pg_restore`): in the EDB PostgreSQL installer
for Windows, tick only "Command Line Tools". Then, after the model-down drill's setup:

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\16\bin"
python -m frontdesk.drills backup-restore --admin-dsn postgresql://postgres:local-only@localhost:5433/frontdesk --app-password local-app --record docs/drills
```

You should see `Result: 4 of 4 steps passed`.

## Run 2: Aurora point-in-time restore in AWS dev (to do after DEPLOY.md step 17)

SEAM(restore-drill-aws): needs the dev account. `scripts/restore-drill.ps1` restores the dev
cluster to its latest restorable time as a **new** cluster (the original is only read), adds a
Serverless v2 writer, turns on the Data API for the copy, runs the same restore check through
the Data API, writes the timings to `docs/drills/<date>-backup-restore-aws.md`, and deletes the
copy even if a step fails. Cost: one 0.5 to 2 ACU instance for about half an hour, roughly R10 to R25.

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\restore-drill.ps1 -Profile fd-dev
```

What you should see: "Restoring ... (the original is not modified)", then after 15 to 30
minutes the restore check table with every row `pass`, then "Recorded in docs\drills\...", then
"Deleting the copy". The record holds the measured RTO (restore requested to verified safe) and
how many minutes before the restore point the newest event was (the measured RPO). Commit the
record and add its link to `docs/DEPTH.md`.

## What the drill taught

- **Database-level grants are not in a dump.** `GRANT CONNECT ON DATABASE` lives outside
  `pg_dump`'s output. Here the app role could connect anyway, because a new database lets
  PUBLIC connect by default; on a hardened server that revokes that default, a logical restore
  would lock the app out. The drill grants it explicitly so it does not depend on the default.
  Point-in-time restore in AWS copies the whole cluster, so this applies to logical restores only.
- **At demo size the numbers are trivial** (an 81 KiB dump, restore in a fraction of a second).
  They say nothing about a client with a year of conversations; the AWS run measures real RTO.
- **A check that cannot fail proves nothing.** Breaking the copy on purpose is now part of the
  drill, so a future change that makes the restore check vacuous fails CI.

## Interview line

"My restore drill doesn't stop at 'the rows came back'. It proves forced RLS, the append-only
triggers and the role limits survived, then breaks the copy to show the check catches it, and
the AWS version measures RTO and RPO from a real point-in-time restore."
