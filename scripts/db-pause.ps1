<#
.SYNOPSIS
  Stops (or starts) the dev Aurora cluster. Only needed if af-south-1 does not support
  Aurora scaling to 0 ACU (scripts/check-regions.ps1). A stopped cluster costs storage only;
  AWS restarts it automatically after 7 days.
.EXAMPLE
  .\scripts\db-pause.ps1 -Profile fd-dev -Action stop
#>
param([string]$Profile = "fd-dev", [ValidateSet("stop", "start")][string]$Action = "stop")
$ErrorActionPreference = "Stop"
$arn = aws cloudformation describe-stacks --stack-name FrontDesk-dev-Data --profile $Profile --region af-south-1 --query "Stacks[0].Outputs[?OutputKey=='ClusterArn'].OutputValue" --output text
$id = $arn.Split(":")[-1]
aws rds "$Action-db-cluster" --db-cluster-identifier $id --profile $Profile --region af-south-1 --query "DBCluster.Status" --output text
