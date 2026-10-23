<#
.SYNOPSIS
  Builds build\lambda for the Lambda runtime (Python 3.13, ARM64). Same as build-lambda.sh.
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path build\lambda) { Remove-Item -Recurse -Force build\lambda }
New-Item -ItemType Directory -Force build\lambda | Out-Null
python -m pip install --quiet --target build\lambda --platform manylinux2014_aarch64 --implementation cp `
  --python-version 3.13 --only-binary=:all: --upgrade "pydantic>=2.7" "numpy>=1.26" "pyyaml>=6.0" "cryptography>=43"
Copy-Item -Recurse backend\frontdesk build\lambda\frontdesk
Copy-Item -Recurse db\migrations build\lambda\frontdesk\migrations
Get-ChildItem build\lambda -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Remove-Item -Force build\lambda\frontdesk\db\local.py
if (Test-Path build\lambda.zip) { Remove-Item -Force build\lambda.zip }
Compress-Archive -Path build\lambda\* -DestinationPath build\lambda.zip
Write-Host "Built build\lambda and build\lambda.zip"
