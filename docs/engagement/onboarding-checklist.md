# Onboarding checklist (operator)

Four parts: the replay that sells the pilot, go-live, the weekly routine, the end. Every
command is PowerShell, from the repository root with the virtual environment active. Every
command that writes runs with `--dry-run` first; read the plan, then run it again with `--write`.

## A. The replay on the client's own questions (laptop only, before any contract)

Nothing goes to AWS or to any model: the offline pipeline, a throwaway local database, and the
export deleted at the end.

```powershell
$Client = "acme"                                   # change this: a short name for the client
$Db = "frontdesk_$Client"
docker compose up -d db
docker compose exec db psql -U postgres -c "CREATE DATABASE $Db"
python -m frontdesk.db.migrate --dsn postgresql://postgres:local-only@localhost:5433/$Db
docker compose exec db psql -U postgres -d $Db -c "ALTER ROLE frontdesk_app LOGIN PASSWORD 'local-app'; GRANT CONNECT ON DATABASE $Db TO frontdesk_app"
$Dsn = "postgresql://frontdesk_app:local-app@localhost:5433/$Db"
python -m frontdesk.admin create-tenant --dsn $Dsn --name "Acme Solar" --slug $Client --write
```

You should see `tenant id: <uuid> (local only)`. Then, with that id, the client's documents (Markdown
files with `title`, `approved_by` and `review_by` at the top) and their staff:

```powershell
$Tenant = "paste the tenant id here"
python -m frontdesk.admin load-docs --dsn $Dsn --tenant $Tenant --dir C:\clients\acme\docs --approved-by "Owner Name" --embedder hashing-v1 --dry-run
python -m frontdesk.admin load-docs --dsn $Dsn --tenant $Tenant --dir C:\clients\acme\docs --approved-by "Owner Name" --embedder hashing-v1 --write
python -m frontdesk.admin create-staff --dsn $Dsn --tenant $Tenant --email owner@acme.example --name "Owner Name" --role owner --topics pricing,discount,general --write
python -m frontdesk.replay --dsn $Dsn --tenant $Tenant --input C:\clients\acme\chats.txt --whatsapp-business-name "Acme Solar" --out reports\$Client
```

You should see `replayed N questions; report: reports\acme\owner-report.md`. Check the dry run
listed every document with a review date, and that no chunk was QUARANTINED (if one was, read it:
text addressed to the assistant inside a document is either an attack or a badly written FAQ).

Then the ROI sheet: copy `docs/engagement/roi-inputs.demo.yaml` to
`reports\acme\roi-inputs.acme.yaml`, point `replay:` at `reports/acme/replay.jsonl`, fill the
assumptions from the workshop with their sources, and run `python -m frontdesk_ml.roi reports\acme\roi-inputs.acme.yaml`.

Clean up the same day the report is sent:

```powershell
docker compose exec db psql -U postgres -c "DROP DATABASE $Db WITH (FORCE)"
Remove-Item -Recurse -Force C:\clients\acme\chats.txt
```

## B. Go-live (after the pilot charter is signed)

1. Prod deployed and signed (`docs/DEPLOY.md` step 19). Tenant, documents and staff created as in
   part A, but with `--profile fd-prod` instead of `--dsn`, and without `--embedder` (Cohere is the default).
2. Each staff member signs in to the inbox and sets up two-factor authentication.
3. Evaluation on the client's documents: write 30 to 50 of their real questions (from the replay,
   redacted) into an evaluation file in the same format as `evals/datasets/bushveld.synthetic.yaml`,
   and run the release evals on it. Go live only if it passes the Bedrock floors in `docs/PLAN.md`.
4. Order lookup (optional): their developer runs the conformance kit
   (`docs/engagement/integration-contract.md`); with 8 of 8, connect it:
   `python -m frontdesk.admin connect-orders-api --profile fd-prod --tenant $Tenant --base-url https://orders.acme.example/frontdesk --by "Machethe DM" --dry-run`.
5. Channel: the WhatsApp number (`docs/DEPLOY.md` step 18) or the website widget
   (`python -m frontdesk.admin create-widget-key --profile fd-prod --tenant $Tenant --origin https://www.acme.example --dry-run`).
6. Send a test message from the operator's phone; check the greeting says it is an automated
   assistant, ask for a person, and see it arrive in the inbox with a name on it.

## C. Every week of the pilot (30 minutes, Monday)

- Insights screen: answered after hours, handoff rate with its interval, documents due for
  review, and the questions the assistant could not answer. Send the owner the top three gaps.
- DET-12 (intent drift) and the operations dashboard: anything unusual?
- The shadow classifier's agreement with the rules (event payloads, `shadow`): note it; it decides
  whether a retrained model is ever worth testing.
- Anything the owner wants changed: a document edit (reload with `load-docs`, a new version is
  created and the old one retired), a routing change, or a pause.

## D. The end (day 30, or whenever either side stops)

```powershell
python -m frontdesk.admin pause-assistant --profile fd-prod --tenant $Tenant --reason "OFFBOARD end of pilot" --by "Machethe DM" --write
python -m frontdesk.admin offboard-export --profile fd-prod --tenant $Tenant --out C:\handover\acme --by "Machethe DM"
python -m frontdesk.admin offboard-erase --profile fd-prod --tenant $Tenant --manifest-sha256 paste-the-hash --pool $Pool --by "Machethe DM" --dry-run
```

Hand over the export (encrypted), get the client's confirmation that they have it, then run the
erase with `--write` and send them the certificate it writes. Delete your copy of the export.
