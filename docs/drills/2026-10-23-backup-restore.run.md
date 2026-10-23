# Drill: back up, restore, and prove the restore is safe (23 October 2026)

Run: `python -m frontdesk.drills backup-restore` against **local Postgres (pg_dump and pg_restore)**, demo tenant only, at 05:00 UTC.
Result: **4 of 4 steps passed**.

| # | Step | Must happen | What happened | Pass | ms |
|---|---|---|---|---|---|
| 1 | Back up the database | a complete logical backup is written | 81 KiB custom-format dump | yes | 110.2 |
| 2 | Restore it into a new database | pg_restore finishes with no error | restored into frontdesk_restored_36fb11 | yes | 239.2 |
| 3 | Check the restored copy | migrations, forced RLS, append-only triggers, role limits, tenant data all verified | 6 of 6 checks passed | yes | 74.5 |
| 4 | Break the copy on purpose | the check fails and names the table without forced RLS and the disabled trigger | 2 checks failed, naming: app.messages: missing rls_forced audit_log.audit_log_append_only | yes | 56.4 |
