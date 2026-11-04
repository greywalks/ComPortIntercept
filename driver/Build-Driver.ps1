#requires -version 5.1
$ErrorActionPreference = 'Stop'

$driverDir = $PSScriptRoot
$sln = Join-Path $driverDir 'ZebraUsbTap.sln'
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw 'Visual Studio 2022 / vswhere.exe was not found.' }

$vsPath = & $vswhere -latest -products * -version '[17.0,18.0)' -property installationPath
if (-not $vsPath) { throw 'Visual Studio 2022 was not found.' }
$msbuild = Join-Path $vsPath 'MSBuild\Current\Bin\amd64\MSBuild.exe'
if (-not (Test-Path $msbuild)) { throw "64-bit MSBuild was not found: $msbuild" }

$kitRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Include'
$kits = @()
if (Test-Path $kitRoot) {
    $kits = Get-ChildItem $kitRoot -Directory | Where-Object {
        Test-Path (Join-Path $_.FullName 'km\ntddk.h')
    } | Sort-Object { [version]$_.Name } -Descending
}
if (-not $kits) { throw 'No Windows 10/11 WDK include tree containing km\ntddk.h was found.' }
$sdk = $kits[0].Name

Write-Host "Visual Studio: $vsPath" -ForegroundColor Cyan
Write-Host "WDK/SDK:       $sdk" -ForegroundColor Cyan
Write-Host 'Building Debug|x64...' -ForegroundColor Cyan

& $msbuild $sln /t:Rebuild /p:Configuration=Debug /p:Platform=x64 /p:WindowsTargetPlatformVersion=$sdk /m
if ($LASTEXITCODE -ne 0) { throw "Driver build failed with exit code $LASTEXITCODE" }

$sys = Join-Path $driverDir 'x64\Debug\ZebraUsbTap.sys'
$inf = Join-Path $driverDir 'x64\Debug\ZebraUsbTap\ZebraUsbTap.inf'
$cat = Join-Path $driverDir 'x64\Debug\ZebraUsbTap\zebrausbtap.cat'
foreach ($p in @($sys,$inf,$cat)) {
    if (-not (Test-Path $p)) { throw "Build reported success, but expected package file is missing: $p" }
}

Write-Host "`nBuild succeeded." -ForegroundColor Green
Get-Item $sys,$inf,$cat | Select-Object FullName,Length | Format-Table -AutoSize
Write-Host "`nSignatures:" -ForegroundColor Cyan
Get-AuthenticodeSignature $sys,$cat | Select-Object Path,Status,StatusMessage | Format-Table -AutoSize
