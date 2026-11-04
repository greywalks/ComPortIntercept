#requires -version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content (Join-Path $root 'config.json') -Raw | ConvertFrom-Json
$tokenFile = Join-Path $root 'data\private\api-token'
if (-not (Test-Path $tokenFile)) { throw 'API token not found. Start the archive and run this script elevated.' }
$token = (Get-Content $tokenFile -Raw).Trim()
$headers = @{ Authorization = "Bearer $token" }
$base = "http://127.0.0.1:$($config.listen_port)"

Write-Host 'Authenticated status:' -ForegroundColor Cyan
Invoke-RestMethod -Uri "$base/api/status" -Headers $headers -TimeoutSec 5 | ConvertTo-Json -Depth 6
if (-not $config.enable_test_ingest) {
    Write-Host 'Test ingestion is disabled. Set enable_test_ingest to true and restart the archive to run the optional API-only insertion test.' -ForegroundColor Yellow
    return
}
$zpl = '^XA^FO40,40^A0N,30,30^FDZebra Archive Diagnostic^FS^XZ'
$body = @{ target_id = 1; base64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($zpl)) } | ConvertTo-Json
Invoke-RestMethod -Uri "$base/api/ingest" -Method Post -Headers $headers -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 5
Write-Host 'This verifies only the web/database/archive path, not USB capture or physical printing.' -ForegroundColor Yellow
