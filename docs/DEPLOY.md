# Deploy guide (Windows, PowerShell)

Follow the steps in order. Each one says what to paste and what you should see. Where a step
happens in the AWS console, it says which page. Nothing here needs an IAM user or an access
key: you sign in with IAM Identity Center, and GitHub signs in with OIDC.

Placeholders look like `<THIS>`. Keep a private note (not in this repo, which is public) with
your account ids and e-mail addresses as you go.

---

## 0. Tools on your laptop (once)

```powershell
winget install -e --id Amazon.AWSCLI
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Python.Python.3.13
winget install -e --id Git.Git
winget install -e --id GitHub.cli
# Optional, for the local test database:
winget install -e --id Docker.DockerDesktop
```

Close and reopen PowerShell, then check:

```powershell
aws --version; node --version; python --version; git --version; gh --version
```

You should see `aws-cli/2.x`, `v22.x` (or newer LTS), `Python 3.13.x`, a git version and a gh version.

Clone and install:

```powershell
git clone https://github.com/machetheDM/front-desk.git; Set-Location front-desk
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e "backend[dev]" -e "ml[dev]"
Set-Location infra; npm ci; Set-Location ..
```

## 1. Secure the root user (console, management account)

This is the account you already have. It becomes the **management** account.

1. Sign in at <https://console.aws.amazon.com/> as the root user (your e-mail and password).
2. Top right, your account name, **Security credentials**.
3. **Multi-factor authentication (MFA)**, **Assign MFA device**. Use a passkey/security key if you have one, otherwise an authenticator app (not SMS). Name it `root-mfa`.
4. On the same page, under **Access keys**: there must be none. If any exist, **Delete** them.
5. **Account** page (top right menu): set **Alternate contacts** for Billing, Operations and Security to your own e-mail and phone, so AWS can reach you about abuse or billing.

You should see: "MFA device assigned", "No access keys".

From now on, root is only for the break-glass runbook (`docs/INCIDENT-PLAN.md` section 6).

## 2. Turn on the Cape Town Region (console, management account)

af-south-1 is an opt-in Region and is off by default.

1. Top right, your account name, **Account**.
2. Scroll to **AWS Regions**, find **Africa (Cape Town) af-south-1**, **Enable**.

You should see the status go to "Enabling", then "Enabled" (a few minutes).

## 3. Create the organization (console)

1. Open **AWS Organizations** (search bar), **Create an organization**. Keep "All features".
2. Verify the e-mail AWS sends you.
3. On the **AWS accounts** page note the **Root** id (`r-xxxx`). Under **Settings** note the **Organization ID** (`o-xxxxxxxxxx`).
4. **Policies**, **Service control policies**, **Enable service control policies**.

You should see: one account (yours) under Root; SCPs "Enabled".

## 4. IAM Identity Center (console, in Cape Town)

1. Switch the console Region (top right) to **Africa (Cape Town)**.
2. Open **IAM Identity Center**, **Enable**. Choose "Enable with AWS Organizations".
3. **Settings**, **Authentication**, **Multi-factor authentication**, **Configure**:
   - Prompt users for MFA: **Every time they sign in**
   - Users can authenticate with: **Security keys and built-in authenticators** and **Authenticator apps**
   - If a user does not yet have a registered MFA device: **Require them to register an MFA device at sign in**
4. **Users**, **Add user**: your name, your e-mail. Accept the invitation e-mail and register MFA.
5. **Permission sets**, **Create permission set**:
   - Predefined: `AdministratorAccess`, session duration **1 hour**. Name `FrontDeskAdmin`.
   - Predefined: `ReadOnlyAccess`, session duration 4 hours. Name `FrontDeskReadOnly`.
6. **AWS accounts**, select your management account, **Assign users or groups**, choose yourself, `FrontDeskAdmin`.
7. Note the **AWS access portal URL** on the Identity Center dashboard (`https://d-xxxxxxxxxx.awsapps.com/start`).

## 5. Sign in from PowerShell with SSO

```powershell
aws configure sso --profile fd-mgmt
```

Answer the prompts:

- SSO session name: `frontdesk`
- SSO start URL: `<your access portal URL>`
- SSO region: `af-south-1`
- SSO registration scopes: press Enter
- (a browser opens: approve) then choose the management account and `FrontDeskAdmin`
- CLI default client Region: `us-east-1`
- CLI default output format: `json`

Check:

```powershell
aws sts get-caller-identity --profile fd-mgmt
aws iam get-account-summary --profile fd-mgmt --query "SummaryMap.AccountAccessKeysPresent"
```

You should see your management account id with an `AWSReservedSSO_FrontDeskAdmin_...` role, and `0` for root access keys.

## 6. Let Organizations work with CloudTrail and Access Analyzer

```powershell
$env:AWS_PROFILE = "fd-mgmt"
aws organizations enable-aws-service-access --service-principal cloudtrail.amazonaws.com
aws organizations enable-aws-service-access --service-principal access-analyzer.amazonaws.com
aws organizations list-aws-service-access-for-organization --query "EnabledServicePrincipals[].ServicePrincipal"
```

You should see `cloudtrail.amazonaws.com`, `access-analyzer.amazonaws.com` and `sso.amazonaws.com` in the list.

## 7. Create the member accounts and SCPs (CDK, management account)

Each account needs its own e-mail address. With Gmail you can use plus-addressing on your one
inbox: `<you>+fd-security@gmail.com`, `<you>+fd-logs@gmail.com`, `<you>+fd-dev@gmail.com`,
`<you>+fd-prod@gmail.com`.

```powershell
$env:AWS_PROFILE = "fd-mgmt"
$Mgmt = aws sts get-caller-identity --query Account --output text
Set-Location infra
npx cdk bootstrap "aws://$Mgmt/us-east-1"
npx cdk deploy FrontDesk-Organization `
  -c managementAccount=$Mgmt -c alertEmail=<you>@gmail.com `
  -c rootId=<r-xxxx> -c organizationId=<o-xxxxxxxxxx> `
  -c emailSecurity=<you>+fd-security@gmail.com -c emailLogArchive=<you>+fd-logs@gmail.com `
  -c emailDev=<you>+fd-dev@gmail.com -c emailProd=<you>+fd-prod@gmail.com
```

CDK shows the IAM and SCP changes and asks `Do you wish to deploy these changes (y/n)?`. Read
them, then `y`. Account creation takes 5 to 15 minutes. You should see four outputs:
`SecurityAccountId`, `LogArchiveAccountId`, `DevAccountId`, `ProdAccountId`. Copy them into
your private note, and into this session:

```powershell
$Security = "<SecurityAccountId>"; $Logs = "<LogArchiveAccountId>"; $Dev = "<DevAccountId>"; $Prod = "<ProdAccountId>"
```

In the console, **AWS Organizations**: you should see OUs Security (security, log-archive)
and Workloads > Dev (dev), Workloads > Prod (prod), and six `frontdesk-*` SCPs attached.

## 8. Turn on Cape Town in every new account

Member accounts cannot do this themselves (an SCP denies it), so the management account does:

```powershell
foreach ($a in @($Security, $Logs, $Dev, $Prod)) {
  aws account enable-region --account-id $a --region-name af-south-1
}
Start-Sleep -Seconds 120
foreach ($a in @($Security, $Logs, $Dev, $Prod)) {
  aws account get-region-opt-status --account-id $a --region-name af-south-1 --query RegionOptStatus --output text
}
```

You should see `ENABLED` four times (re-run the second loop if you see `ENABLING`).

## 9. Start Meta business verification now (it takes days)

WhatsApp needs a Meta business portfolio. Verification takes days, so start it today, even
though WhatsApp is wired up in Phase 3.

1. Go to <https://business.facebook.com/>, create a **business portfolio** in your name.
2. **Settings**, **Business info**: legal name, address, phone, website (a simple page is fine).
3. **Security centre**, **Start verification**. Meta asks for a document that shows the legal
   name and address. **Decision for you:** if you trade as an individual, Meta may not accept a
   personal document; registering a company or sole-proprietor trading name with CIPC is the usual route. Without verification you can still
   send to a small number of test recipients, which is enough for the demo tenant.
4. Get a **second SIM** for the demo tenant's WhatsApp number. It must receive an SMS or call
   for the one-time code and must not already be registered on WhatsApp.

The AWS side (linking the number in End User Messaging Social) is step 18.

## 10. Give yourself access to the new accounts (console, Identity Center)

**IAM Identity Center**, **AWS accounts**: assign yourself `FrontDeskAdmin` on **security**,
**log-archive** and **dev**, and `FrontDeskReadOnly` on **prod** (prod writes go through the
pipeline; a separate admin assignment for prod is made only during an incident).

```powershell
aws configure sso --profile fd-dev        # choose the dev account, FrontDeskAdmin, Region af-south-1
aws configure sso --profile fd-security   # security account, FrontDeskAdmin, af-south-1
aws configure sso --profile fd-logs       # log-archive account, FrontDeskAdmin, af-south-1
aws configure sso --profile fd-prod-ro    # prod account, FrontDeskReadOnly, af-south-1
aws sts get-caller-identity --profile fd-dev
```

You should see the dev account id.

## 11. Budgets alarms (management account)

```powershell
$env:AWS_PROFILE = "fd-mgmt"
npx cdk deploy FrontDesk-Budgets -c managementAccount=$Mgmt -c alertEmail=<you>@gmail.com
aws budgets describe-budgets --account-id $Mgmt --query "Budgets[].{name:BudgetName,limit:BudgetLimit.Amount}"
```

You should see `frontdesk-monthly-total` with a limit of `16.22` (USD, which is R300 at
R18.50). Alerts go to your e-mail at 50%, 80% and 100% actual, and 100% forecast.

## 12. Organization trail and log archive

```powershell
$Ctx = "-c managementAccount=$Mgmt -c logArchiveAccount=$Logs -c organizationId=<o-xxxxxxxxxx> -c alertEmail=<you>@gmail.com".Split(" ")
npx cdk bootstrap "aws://$Logs/af-south-1" --profile fd-logs
npx cdk deploy FrontDesk-LogArchive --profile fd-logs @Ctx
npx cdk deploy FrontDesk-OrgTrail --profile fd-mgmt @Ctx
aws cloudtrail get-trail-status --name frontdesk-org-trail --region us-east-1 --profile fd-mgmt --query IsLogging
```

You should see `true`. Check your e-mail and **confirm the SNS subscription** for
`frontdesk-security-alerts`; until you click it, alerts are not delivered.

Test detection DET-04/DET-01 later by reading `docs/DETECTIONS.md`; do not test root activity on purpose.

## 13. Security account: Access Analyzer

```powershell
aws organizations register-delegated-administrator --account-id $Security --service-principal access-analyzer.amazonaws.com --profile fd-mgmt
npx cdk bootstrap "aws://$Security/af-south-1" --profile fd-security
npx cdk deploy FrontDesk-Security --profile fd-security -c securityAccount=$Security -c managementAccount=$Mgmt -c alertEmail=<you>@gmail.com
```

Confirm the second SNS e-mail. In the console (security account, af-south-1), **IAM Access
Analyzer** should show `frontdesk-org-external-access` with zone of trust "Organization".

## 14. Check the region matrix against your real account

```powershell
.\scripts\check-regions.ps1 -Profile fd-dev
Get-Content docs\evidence\region-check.json | Select-Object -First 40
```

Look for: `minAcu: 0` for 16.x versions (scale to zero works in Cape Town), Claude ids under
`bedrockModels_euCentral1` and `eu.anthropic...` profiles, only `global.` profiles under
`bedrockProfiles_afSouth1`, a list (possibly empty) rather than an error for
`socialMessaging_afSouth1`, and `AWSLambda-SHA384-ECDSA` for Signer. Commit the file and
tell the next session what it showed: the matrix rows marked *verify* are updated from it.

If `minAcu` is 0.5, deploy with `-c frontdesk:minAcu=0.5` and use `scripts/db-pause.ps1`.

## 15. Bedrock model access (console, dev account, Frankfurt)

Switch Region to **Europe (Frankfurt)**, open **Amazon Bedrock**, **Model access**. If Claude
Haiku 5.5, Claude Sonnet 5.5, Cohere Embed Multilingual, Cohere Rerank 3.5 and Titan Text
Embeddings V2 show "Access granted", nothing to do. Anthropic models ask for a one-time use
case form: describe "customer-service assistant for South African small businesses, grounded
in company documents".

## 16. GitHub: OIDC roles, environments, branch protection

```powershell
npx cdk bootstrap "aws://$Dev/af-south-1" "aws://$Dev/eu-central-1" --profile fd-dev
npx cdk deploy FrontDesk-dev-GithubOidc --profile fd-dev -c devAccount=$Dev
```

For prod, sign in once with an admin assignment (Identity Center: temporarily assign
`FrontDeskAdmin` on prod, profile `fd-prod`), run the same two commands with `$Prod` and
`FrontDesk-prod-GithubOidc`, then remove the assignment.

Copy the role ARNs from the outputs into GitHub (repository variables, not secrets: an ARN is
not a secret):

```powershell
gh auth login
gh variable set AWS_DEV_ACCOUNT --body $Dev
gh variable set AWS_PROD_ACCOUNT --body $Prod
gh variable set AWS_DEV_DEPLOY_ROLE --body "arn:aws:iam::$($Dev):role/frontdesk-github-deploy-dev"
gh variable set AWS_PROD_DEPLOY_ROLE --body "arn:aws:iam::$($Prod):role/frontdesk-github-deploy-prod"
gh variable set AWS_PR_DIFF_ROLE --body "arn:aws:iam::$($Dev):role/frontdesk-github-pr-diff"
gh variable set AWS_EVALS_ROLE --body "arn:aws:iam::$($Dev):role/frontdesk-github-evals"
gh variable set ALERT_EMAIL --body "<you>@gmail.com"
gh api -X PUT repos/machetheDM/front-desk/environments/dev
gh api -X PUT repos/machetheDM/front-desk/environments/evals
gh api -X PUT repos/machetheDM/front-desk/environments/prod -f "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user --jq .id)"
```

Branch protection (require the `ci-passed` check and a pull request on `main`):

```powershell
@'
{"required_status_checks":{"strict":true,"contexts":["ci-passed"]},"enforce_admins":true,
 "required_pull_request_reviews":{"required_approving_review_count":0},"restrictions":null}
'@ | gh api -X PUT repos/machetheDM/front-desk/branches/main/protection --input -
```

In GitHub, **Settings**, **Code security**: turn on **Secret scanning** and **Push protection**,
**Dependabot alerts**.

## 17. Deploy dev

```powershell
.\scripts\build-lambda.ps1
$C = "-c devAccount=$Dev -c alertEmail=<you>@gmail.com".Split(" ")
npx cdk deploy "FrontDesk-dev-*" --profile fd-dev --require-approval broadening @C
aws lambda invoke --function-name frontdesk-dev-migrate --profile fd-dev --region af-south-1 out.json; Get-Content out.json
python -m frontdesk.demo_tenant --target aws --profile fd-dev --dry-run
python -m frontdesk.demo_tenant --target aws --profile fd-dev --write
```

You should see the migrate Lambda return `"RLS catalog check passed"`; the demo-tenant dry run
lists the documents and chunk counts it *would* write; `--write` re-checks that the target is
the demo tenant and writes. After this, pushes to `main` deploy dev automatically.

Create your staff login for the inbox:

```powershell
$Pool = aws cloudformation describe-stacks --stack-name FrontDesk-dev-App --profile fd-dev --region af-south-1 --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text
python -m frontdesk.admin create-staff --profile fd-dev --pool $Pool --tenant demo --email <you>@gmail.com --name "<Your name>" --role owner --dry-run
python -m frontdesk.admin create-staff --profile fd-dev --pool $Pool --tenant demo --email <you>@gmail.com --name "<Your name>" --role owner --write
```

## 18. WhatsApp number (after Meta verification, or with an unverified portfolio)

1. Console, dev account, **Africa (Cape Town)**, **AWS End User Messaging Social**, **Add WhatsApp phone number**. This opens Meta's signup: pick your business portfolio, create a WhatsApp Business Account, add the second SIM's number, receive the code.
2. In End User Messaging Social, open the WhatsApp Business Account, **Event destination**: choose SNS, paste the `WhatsAppTopicArn` output of `FrontDesk-dev-App`.
3. Map the number to the demo tenant (SSM, dry run first):

```powershell
python -m frontdesk.admin map-whatsapp --profile fd-dev --tenant demo --phone-number-id <phone-number-id-...> --dry-run
python -m frontdesk.admin map-whatsapp --profile fd-dev --tenant demo --phone-number-id <phone-number-id-...> --write
```

Send "Hi" from your own phone to the demo number. You should get the assistant's greeting,
which says it is an assistant and that you can ask for a person.

## 19. Prod (when a client signs)

Prod only accepts signed code. The `deploy-prod` workflow does this; to do it by hand:

```powershell
.\scripts\build-lambda.ps1
.\scripts\sign-lambda.ps1 -Profile fd-prod
```

Then run the `deploy-prod` workflow in GitHub (Actions, deploy-prod, Run workflow). It waits
for your approval on the `prod` environment.

## 20. Evidence runs once dev is up (each closes a seam in docs/DEPTH.md)

Run these in order after step 17, with the virtual environment active. Each writes a file under
`docs/` or `reports/`; commit the `docs/` ones and update the matching row in `docs/DEPTH.md`.

1. **Bedrock quotas for the pilot's peak** (Well-Architected reliability item). You should see
   the on-demand requests-per-minute and tokens-per-minute quotas for the Claude models in
   Frankfurt; the pilot needs a few requests a minute.

   ```powershell
   aws service-quotas list-service-quotas --service-code bedrock --region eu-central-1 --profile fd-dev --query "Quotas[?contains(QuotaName, 'Claude')].[QuotaName,Value]" --output table
   ```

2. **Release evals on Bedrock** (quality floors, red-team on the real model, promptfoo): GitHub,
   Actions, `release-evals`, Run workflow. You should see the `bedrock` job pass the Bedrock
   floors in `docs/PLAN.md` section 5; its report artifact holds the numbers for
   `docs/MODEL-CARD.md` section 5.

3. **The LLM arm of the classifier comparison**. You should see a fourth row, "LLM (Claude
   Haiku via Bedrock)", in `docs/evidence/classifier-comparison.md`, with its cost per 1,000.

   ```powershell
   $env:AWS_PROFILE = "fd-dev"
   python -m frontdesk_ml.classifier compare --llm
   ```

4. **Thirty human labels**, so the judge's agreement with a person (Cohen's kappa) can be
   reported. About 15 minutes; you answer "correct or not" for each shown answer.

   ```powershell
   python -m frontdesk_ml.label --replay reports/local/replay.jsonl --pipeline offline --labeller "Machethe DM"
   ```

5. **Model-down drill in AWS**: follow `docs/drills/2026-10-23-model-down.md`, "Run 2". You
   should get the DET-06 and DET-02 e-mails.

6. **Point-in-time restore drill in AWS**: about 30 minutes and R10 to R25. You should see every
   restore-check row `pass` and a new `docs/drills/<date>-backup-restore-aws.md` with the
   measured RTO and RPO.

   ```powershell
   .\scripts\restore-drill.ps1 -Profile fd-dev
   ```

7. **ZAP against the dev site**: in GitHub, Settings, Secrets and variables, Actions, Variables,
   add `DEV_WEB_URL` = the CloudFront URL from step 17 (no trailing slash). Then Actions, `zap`,
   Run workflow. You should see the baseline scan pass, or an issue opened listing findings.
