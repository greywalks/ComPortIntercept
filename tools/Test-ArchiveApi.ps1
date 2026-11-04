#requires -version 5.1
$ErrorActionPreference = 'Stop'

Write-Host 'Status:' -ForegroundColor Cyan
$status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/status' -TimeoutSec 5
$status | ConvertTo-Json -Depth 6

$zpl = '^XA^FO40,40^A0N,30,30^FDZebra Archive Diagnostic^FS^XZ'
$body = @{
    target_id = 1
    base64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($zpl))
} | ConvertTo-Json

Write-Host "`nInjecting one API-only diagnostic record..." -ForegroundColor Cyan
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/ingest' -Method Post -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 5
Write-Host 'This verifies the web/database/archive path only. It does NOT verify USB capture.' -ForegroundColor Yellow
