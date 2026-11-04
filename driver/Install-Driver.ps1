#requires -version 5.1
param(
    [string]$PackageInf = (Join-Path $PSScriptRoot 'x64\Debug\ZebraUsbTap\ZebraUsbTap.inf')
)
$ErrorActionPreference = 'Stop'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run PowerShell as Administrator.' }
if (-not (Test-Path $PackageInf)) { throw "Package INF not found: $PackageInf. Run Build-Driver.ps1 first." }

$packageDir = Split-Path -Parent $PackageInf
$sys = Join-Path (Split-Path -Parent $packageDir) 'ZebraUsbTap.sys'
$cat = Join-Path $packageDir 'zebrausbtap.cat'
foreach ($p in @($sys,$cat)) {
    if (-not (Test-Path $p)) { throw "Missing package file: $p" }
    $sig = Get-AuthenticodeSignature $p
    Write-Host "$p -> $($sig.Status)" -ForegroundColor $(if($sig.Status -eq 'Valid'){'Green'}else{'Yellow'})
}

Write-Host "`nInstalling package..." -ForegroundColor Cyan
$out = & pnputil.exe /add-driver $PackageInf /install 2>&1
$out | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { throw "pnputil failed with exit code $LASTEXITCODE" }

$stateDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.local'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$published = ($out | Select-String -Pattern 'Published Name:\s+(oem\d+\.inf)' | Select-Object -First 1).Matches.Groups[1].Value
if ($published) { Set-Content -Path (Join-Path $stateDir 'published-inf.txt') -Value $published -Encoding ASCII }

$hardware = @('USB\VID_0A5F&PID_0080','USB\VID_0A5F&PID_0062')
foreach ($h in $hardware) {
    Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "$h*" } | ForEach-Object {
        Write-Host "Restarting $($_.InstanceId)" -ForegroundColor Cyan
        & pnputil.exe /restart-device $_.InstanceId
    }
}

Write-Host "`nDriver state:" -ForegroundColor Cyan
& sc.exe query ZebraUsbTap
Write-Host @'

If ZebraUsbTap remains STOPPED, run:
  sc.exe start ZebraUsbTap

If that returns error 577, the package is test-signed but Windows kernel signature
enforcement is active. Do not repeatedly reinstall the package. Use the documented
TEST_SIGNING.md procedure on a designated test/development machine, or deploy a
properly Microsoft-signed production package.
'@ -ForegroundColor Yellow
