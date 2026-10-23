<#
.SYNOPSIS
  Settles the "verify" rows of the region matrix (docs/ARCHITECTURE.md section 2) against the
  real AWS account, and writes docs/evidence/region-check.json.
  SEAM(region-check): needs the owner's account; commit the file it writes.

.DESCRIPTION
  Run after Identity Center is set up (docs/DEPLOY.md step 14). Read-only: it only lists and
  describes. Commit the JSON it writes; the region matrix cites it.

.EXAMPLE
  .\scripts\check-regions.ps1 -Profile fd-dev
#>
param(
  [string]$Profile = "fd-dev",
  [string]$Out = "docs/evidence/region-check.json"
)
$ErrorActionPreference = "Continue"

function Try-Aws([string[]]$AwsArgs) {
  # Returns parsed JSON, or the error text: an error is evidence too (e.g. "no endpoint").
  $raw = & aws @AwsArgs --profile $Profile --output json 2>&1
  if ($LASTEXITCODE -eq 0) {
    try { return ($raw | Out-String | ConvertFrom-Json) } catch { return ($raw | Out-String).Trim() }
  }
  return @{ error = ($raw | Out-String).Trim() }
}

$result = [ordered]@{
  checkedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
  account      = (aws sts get-caller-identity --profile $Profile --query Account --output text)
}

Write-Host "Aurora PostgreSQL 16 in af-south-1: does Serverless v2 go down to 0 ACU?"
$result.auroraServerlessV2_afSouth1 = Try-Aws @("rds", "describe-db-engine-versions", "--engine", "aurora-postgresql",
  "--region", "af-south-1", "--query",
  "DBEngineVersions[?starts_with(EngineVersion,'16.')].{version:EngineVersion,minAcu:ServerlessV2FeaturesSupport.MinCapacity,maxAcu:ServerlessV2FeaturesSupport.MaxCapacity}")

Write-Host "Bedrock: Claude, embedding and rerank models in eu-central-1 (and how they can be invoked)"
$result.bedrockModels_euCentral1 = Try-Aws @("bedrock", "list-foundation-models", "--region", "eu-central-1", "--query",
  "modelSummaries[?contains(modelId,'claude-haiku-5-5') || contains(modelId,'claude-sonnet-5-5') || contains(modelId,'embed') || contains(modelId,'rerank')].{id:modelId,invoke:inferenceTypesSupported}")
$result.bedrockProfiles_euCentral1 = Try-Aws @("bedrock", "list-inference-profiles", "--region", "eu-central-1", "--query",
  "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].{id:inferenceProfileId,regions:models[].modelArn}")

Write-Host "Bedrock in af-south-1: which Claude profiles exist there (expected: global only)"
$result.bedrockProfiles_afSouth1 = Try-Aws @("bedrock", "list-inference-profiles", "--region", "af-south-1", "--query",
  "inferenceProfileSummaries[?contains(inferenceProfileId,'claude')].inferenceProfileId")
$result.bedrockModels_afSouth1 = Try-Aws @("bedrock", "list-foundation-models", "--region", "af-south-1", "--query",
  "modelSummaries[?contains(modelId,'embed') || contains(modelId,'rerank')].modelId")

Write-Host "End User Messaging Social (WhatsApp) in af-south-1"
$result.socialMessaging_afSouth1 = Try-Aws @("socialmessaging", "list-linked-whatsapp-business-accounts", "--region", "af-south-1")

Write-Host "AWS Signer Lambda platform in af-south-1"
$result.signer_afSouth1 = Try-Aws @("signer", "list-signing-platforms", "--region", "af-south-1", "--query",
  "platforms[?platformId=='AWSLambda-SHA384-ECDSA'].platformId")

Write-Host "Amplify Hosting in af-south-1 (expected: no endpoint)"
$result.amplify_afSouth1 = Try-Aws @("amplify", "list-apps", "--region", "af-south-1")

Write-Host "IAM Identity Center instance"
$result.identityCenter_afSouth1 = Try-Aws @("sso-admin", "list-instances", "--region", "af-south-1")

$result | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $Out
Write-Host ""
Write-Host "Wrote $Out. Commit it:  git add $Out; git commit -m 'Record region check'"
