<#
.SYNOPSIS
  Uploads build\lambda.zip to the signing bucket, signs it with AWS Signer, and prints the
  -c flags for a prod deploy. Prod's CodeSigningConfig rejects anything else.
.EXAMPLE
  .\scripts\sign-lambda.ps1 -Profile fd-prod -Env prod
#>
param([string]$Profile = "fd-prod", [string]$Env = "prod")
$ErrorActionPreference = "Stop"
$stack = "FrontDesk-$Env-Signing"
$outs = aws cloudformation describe-stacks --stack-name $stack --profile $Profile --region af-south-1 --query "Stacks[0].Outputs" | ConvertFrom-Json
$bucket = ($outs | Where-Object OutputKey -eq "ArtifactsBucket").OutputValue
$profileName = ($outs | Where-Object OutputKey -eq "SigningProfileName").OutputValue
$key = "unsigned/lambda-$(Get-Date -Format yyyyMMddHHmmss).zip"
$version = aws s3api put-object --bucket $bucket --key $key --body build\lambda.zip --profile $Profile --region af-south-1 --query VersionId --output text
$job = aws signer start-signing-job --source "s3={bucketName=$bucket,key=$key,version=$version}" `
  --destination "s3={bucketName=$bucket,prefix=signed/}" --profile-name $profileName --profile $Profile --region af-south-1 --query jobId --output text
Write-Host "Signing job $job ..."
do { Start-Sleep -Seconds 3; $s = aws signer describe-signing-job --job-id $job --profile $Profile --region af-south-1 | ConvertFrom-Json } while ($s.status -eq "InProgress")
if ($s.status -ne "Succeeded") { throw "signing failed: $($s.statusReason)" }
$signedKey = $s.signedObject.s3.key
$signedVersion = aws s3api head-object --bucket $bucket --key $signedKey --profile $Profile --region af-south-1 --query VersionId --output text
Write-Host "Signed. Deploy prod with:"
Write-Host "  -c frontdesk:signedCodeBucket=$bucket -c frontdesk:signedCodeKey=$signedKey -c frontdesk:signedCodeVersion=$signedVersion"
