# Parses (never runs) every PowerShell script and every ```powershell block in the docs, so a
# command handed to the owner cannot have a syntax error. Bash-isms are caught separately by
# backend/tests/test_docs.py, because `&&` is valid PowerShell 7 but not Windows PowerShell 5.1.
$failed = 0
function Test-Source([string]$text, [string]$where) {
  $errors = $null
  [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$null, [ref]$errors) | Out-Null
  foreach ($e in $errors) {
    $script:failed++
    Write-Host "::error file=$where,line=$($e.Extent.StartLineNumber)::$($e.Message)"
  }
}
Get-ChildItem -Recurse -Filter *.ps1 -Path scripts | ForEach-Object { Test-Source (Get-Content -Raw $_.FullName) $_.FullName }
$docs = @(Get-ChildItem -Recurse -Filter *.md -Path docs) + @(Get-Item AGENTS.md) + @(Get-ChildItem -Filter README.md)
$blocks = 0
foreach ($f in $docs) {
  $text = Get-Content -Raw $f.FullName
  foreach ($m in [regex]::Matches($text, '(?ms)^```powershell\r?\n(.*?)^```')) {
    $blocks++
    Test-Source $m.Groups[1].Value $f.FullName
  }
}
Write-Host "parsed $((Get-ChildItem -Recurse -Filter *.ps1 -Path scripts).Count) scripts and $blocks documented PowerShell blocks; $failed syntax error(s)"
if ($failed) { exit 1 }
