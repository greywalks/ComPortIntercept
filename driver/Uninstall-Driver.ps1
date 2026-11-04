#requires -version 5.1
param([string]$PublishedName)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Driver-Package.ps1')
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run PowerShell as Administrator.' }

$state = Join-Path (Split-Path -Parent $PSScriptRoot) '.local\published-inf.txt'
if (-not $PublishedName -and (Test-Path -LiteralPath $state)) {
    $PublishedName = (Get-Content -LiteralPath $state -Raw).Trim()
}
# Resolve from current driver-store inventory, even when an OEM name was supplied.
$verified = Resolve-ZebraDriverPackage -Packages @(Get-ZebraDriverPackages) -PublishedName $PublishedName
Write-Host "Removing verified ZebraUsbTap package $verified ..." -ForegroundColor Yellow
& pnputil.exe /delete-driver $verified /uninstall
if ($LASTEXITCODE -ne 0) { throw "pnputil removal failed with exit code $LASTEXITCODE" }
if (Test-Path -LiteralPath $state) {
    try {
        if ((Get-Content -LiteralPath $state -Raw).Trim() -ieq $verified) { Remove-Item -LiteralPath $state -Force }
    } catch { Write-Warning "Package removed, but could not clear saved package state: $($_.Exception.Message)" }
}
Restart-ZebraDevices -HardwareIds @('USB\VID_0A5F&PID_0080','USB\VID_0A5F&PID_0062')
Write-Host "`nCurrent driver state:" -ForegroundColor Cyan
& sc.exe query ZebraUsbTap
Write-Host 'If the old service remains RUNNING, reboot before installing a replacement build.' -ForegroundColor Yellow
