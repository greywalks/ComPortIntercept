#requires -version 5.1
param(
    [string]$PackageInf = (Join-Path $PSScriptRoot 'x64\Debug\ZebraUsbTap\ZebraUsbTap.inf')
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Driver-Package.ps1')
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run PowerShell as Administrator.' }
$package = Get-ZebraPackageFiles -PackageInf $PackageInf
foreach ($p in @($package.Sys,$package.Cat)) {
    $sig = Get-AuthenticodeSignature -LiteralPath $p
    Write-Host "$p -> $($sig.Status)" -ForegroundColor $(if($sig.Status -eq 'Valid'){'Green'}else{'Yellow'})
}
Write-Host 'Windows validates the signed catalog and package membership during staging; inspect signing policy before installation.' -ForegroundColor Yellow
Write-Host "`nInstalling package..." -ForegroundColor Cyan
& pnputil.exe /add-driver $PackageInf /install
if ($LASTEXITCODE -ne 0) { throw "pnputil failed with exit code $LASTEXITCODE" }

$stateDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.local'
$state = Join-Path $stateDir 'published-inf.txt'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
# Never preserve stale state if inventory is ambiguous or unavailable.
if (Test-Path -LiteralPath $state) { Remove-Item -LiteralPath $state -Force }
try {
    $published = Resolve-ZebraDriverPackage -Packages @(Get-ZebraDriverPackages)
    Set-Content -Path $state -Value $published -Encoding ASCII
    Write-Host "Verified published package: $published"
} catch {
    Write-Warning "Installed package could not be uniquely identified; inspect Get-WindowsDriver before uninstalling. $($_.Exception.Message)"
}
Restart-ZebraDevices -HardwareIds @('USB\VID_0A5F&PID_0080','USB\VID_0A5F&PID_0062')
Write-Host "`nDriver state:" -ForegroundColor Cyan
& sc.exe query ZebraUsbTap
if ($LASTEXITCODE -ne 0) { throw "Could not query driver state ($LASTEXITCODE)." }
Write-Host 'Verify filter attachment, signing policy, and physical pass-through before capture testing.' -ForegroundColor Yellow
