#requires -version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py.exe -ErrorAction SilentlyContinue }
if (-not $python) { throw 'Python was not found in PATH.' }

Write-Host 'Starting Zebra USB Print Archive...' -ForegroundColor Cyan
Write-Host 'UI: http://127.0.0.1:8765' -ForegroundColor Cyan
& $python.Source '.\server\app.py'
