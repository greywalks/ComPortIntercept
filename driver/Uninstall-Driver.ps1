#requires -version 5.1
param([string]$PublishedName)
$ErrorActionPreference = 'Stop'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run PowerShell as Administrator.' }

if (-not $PublishedName) {
    $state = Join-Path (Split-Path -Parent $PSScriptRoot) '.local\published-inf.txt'
    if (Test-Path $state) { $PublishedName = (Get-Content $state -Raw).Trim() }
}
if (-not $PublishedName) {
    $enum = (& pnputil.exe /enum-drivers 2>&1) -join "`n"
    $blocks = $enum -split "(?:\r?\n){2,}"
    foreach ($b in $blocks) {
        if ($b -match 'Original Name:\s*ZebraUsbTap\.inf' -and $b -match 'Published Name:\s*(oem\d+\.inf)') {
            $PublishedName = $Matches[1]
            break
        }
    }
}
if (-not $PublishedName) { throw 'Could not determine the installed OEM INF. Pass -PublishedName oemNN.inf.' }

Write-Host "Removing $PublishedName ..." -ForegroundColor Yellow
& pnputil.exe /delete-driver $PublishedName /uninstall
if ($LASTEXITCODE -ne 0) { throw "pnputil removal failed with exit code $LASTEXITCODE" }

$hardware = @('USB\VID_0A5F&PID_0080','USB\VID_0A5F&PID_0062')
foreach ($h in $hardware) {
    Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "$h*" } | ForEach-Object {
        & pnputil.exe /restart-device $_.InstanceId
    }
}

Write-Host "`nCurrent driver state:" -ForegroundColor Cyan
& sc.exe query ZebraUsbTap
Write-Host 'If the service remains RUNNING after package removal, the old kernel image is still resident. A reboot is required before installing a replacement build.' -ForegroundColor Yellow
