<#
.SYNOPSIS
  Point-in-time restore drill for the dev Aurora cluster: restore a copy, verify it, time it, delete it.

.DESCRIPTION
  Restores the dev cluster to its latest restorable time as a NEW cluster, adds a Serverless v2
  writer, enables the Data API on the copy, runs `python -m frontdesk.restore_check` against it
  (migrations, forced RLS, append-only triggers, app-role limits, tenant data, data lost), records
  the timings in docs/drills, then deletes the copy. The original cluster is only read.

  Run from the repository root with the virtual environment active (docs/DEPLOY.md step 2).
  Cost: one Serverless v2 instance at 0.5 to 2 ACU for roughly 20 to 40 minutes, about R10 to R25.

.EXAMPLE
  .\scripts\restore-drill.ps1 -Profile fd-dev
#>
param(
  [string]$Profile = "fd-dev",
  [string]$Env = "dev",
  [switch]$KeepCopy
)
# SEAM(restore-drill-aws): runs only against a deployed dev account (docs/DEPLOY.md step 20).
$ErrorActionPreference = "Stop"
$region = "af-south-1"
if ($Env -ne "dev") { throw "Restore drills run in dev only." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Activate the virtual environment first: .\.venv\Scripts\Activate.ps1" }

function Invoke-Aws {
  $out = & aws @args --profile $Profile --region $region --output json
  if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed (exit $LASTEXITCODE)" }
  if ($out) { return ($out | Out-String | ConvertFrom-Json) }
}

$outs = @{}
(Invoke-Aws cloudformation describe-stacks --stack-name "FrontDesk-$Env-Data").Stacks[0].Outputs | ForEach-Object { $outs[$_.OutputKey] = $_.OutputValue }
$sourceId = $outs.ClusterArn.Split(':')[-1]
$src = (Invoke-Aws rds describe-db-clusters --db-cluster-identifier $sourceId).DBClusters[0]
$restorePoint = ([datetime]$src.LatestRestorableTime).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$copyId = "frontdesk-$Env-restore-drill"
$writer = "$copyId-writer"
Write-Host "Restoring $sourceId to $restorePoint as $copyId (the original is not modified)"

$t0 = Get-Date
$checkExit = -1
try {
  Invoke-Aws rds restore-db-cluster-to-point-in-time --source-db-cluster-identifier $sourceId --db-cluster-identifier $copyId `
    --restore-to-time $restorePoint --db-subnet-group-name $src.DBSubnetGroup `
    --vpc-security-group-ids ($src.VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId }) `
    --kms-key-id $src.KmsKeyId --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=2 --no-deletion-protection | Out-Null
  aws rds wait db-cluster-available --db-cluster-identifier $copyId --profile $Profile --region $region
  Invoke-Aws rds create-db-instance --db-instance-identifier $writer --db-cluster-identifier $copyId `
    --engine aurora-postgresql --db-instance-class db.serverless | Out-Null
  aws rds wait db-instance-available --db-instance-identifier $writer --profile $Profile --region $region
  $t1 = Get-Date
  $copyArn = (Invoke-Aws rds describe-db-clusters --db-cluster-identifier $copyId).DBClusters[0].DBClusterArn
  Invoke-Aws rds enable-http-endpoint --resource-arn $copyArn | Out-Null
  Start-Sleep -Seconds 30   # the Data API takes a moment to accept calls on a new endpoint

  $check = python -m frontdesk.restore_check --data-api --cluster-arn $copyArn --admin-secret-arn $outs.AdminSecretArn `
    --app-secret-arn $outs.AppSecretArn --profile $Profile --tenant demo --restore-time $restorePoint
  $checkExit = $LASTEXITCODE
  $t2 = Get-Date

  $date = Get-Date -Format "yyyy-MM-dd"
  $record = @(
    "# Drill: Aurora point-in-time restore in AWS dev ($date)", "",
    "Run: ``.\scripts\restore-drill.ps1 -Profile $Profile``. Restore point (latest restorable time): $restorePoint.", "",
    "| Stage | Minutes |", "|---|---|",
    ("| Restore requested to writer available | {0:N1} |" -f ($t1 - $t0).TotalMinutes),
    ("| Restore check (Data API) | {0:N1} |" -f ($t2 - $t1).TotalMinutes),
    ("| **RTO measured: restore requested to verified safe** | **{0:N1}** |" -f ($t2 - $t0).TotalMinutes), "",
    "Restore check result (exit $checkExit; 0 means every check passed):", "") + $check
  $path = "docs\drills\$date-backup-restore-aws.md"
  $record | Out-File -Encoding utf8 $path
  Write-Host "Recorded in $path"
}
finally {
  if (-not $KeepCopy) {
    Write-Host "Deleting the copy $copyId (the original is untouched)"
    aws rds delete-db-instance --db-instance-identifier $writer --profile $Profile --region $region 2>$null | Out-Null
    aws rds wait db-instance-deleted --db-instance-identifier $writer --profile $Profile --region $region 2>$null
    aws rds delete-db-cluster --db-cluster-identifier $copyId --skip-final-snapshot --profile $Profile --region $region 2>$null | Out-Null
  }
}
if ($checkExit -ne 0) { throw "The restored copy failed the restore check: read the record before trusting this backup." }
