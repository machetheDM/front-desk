<#
.SYNOPSIS
  Stops the assistant answering one sender (abuse, DET-07/DET-09). Dry run by default.
.EXAMPLE
  .\scripts\block-sender.ps1 -Profile fd-dev -Tenant demo -Number +27820000000
  .\scripts\block-sender.ps1 -Profile fd-dev -Tenant demo -Number +27820000000 -Write
#>
param([string]$Profile = "fd-dev", [string]$Tenant = "demo", [Parameter(Mandatory = $true)][string]$Number, [switch]$Write)
$flag = if ($Write) { "--write" } else { "--dry-run" }
python -m frontdesk.admin --profile $Profile block-sender --tenant $Tenant --number $Number $flag
