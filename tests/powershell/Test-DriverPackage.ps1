$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\driver\Driver-Package.ps1')
$zebra = [pscustomobject]@{Driver='oem42.inf'; OriginalFileName='C:\DriverStore\ZebraUsbTap.inf'; ProviderName='Local Zebra Archive'; ClassName='Extension'}
$other = [pscustomobject]@{Driver='oem7.inf'; OriginalFileName='C:\DriverStore\Other.inf'; ProviderName='Other'; ClassName='Printer'}
if ((Resolve-ZebraDriverPackage -Packages @($zebra,$other) -PublishedName 'oem42.inf') -ne 'oem42.inf') { throw 'Valid package rejected' }
foreach ($name in @('oem7.inf', 'bad/path.inf', 'oem999.inf')) {
    $rejected = $false
    try { [void](Resolve-ZebraDriverPackage -Packages @($zebra,$other) -PublishedName $name) } catch { $rejected = $true }
    if (-not $rejected) { throw "Unsafe package accepted: $name" }
}
$second = [pscustomobject]@{Driver='oem43.inf'; OriginalFileName='C:\DriverStore\ZebraUsbTap.inf'; ProviderName='Local Zebra Archive'; ClassName='Extension'}
$ambiguousRejected = $false
try { [void](Resolve-ZebraDriverPackage -Packages @($zebra,$second)) } catch { $ambiguousRejected = $true }
if (-not $ambiguousRejected) { throw 'Ambiguous package list accepted' }
$temp = Join-Path ([IO.Path]::GetTempPath()) ('ZebraPackageTest-' + [guid]::NewGuid())
try {
    $packageDir = Join-Path $temp 'package'
    New-Item -ItemType Directory -Path $packageDir -Force | Out-Null
    $inf = Join-Path $packageDir 'ZebraUsbTap.inf'
    New-Item -ItemType File -Path $inf, (Join-Path $packageDir 'zebrausbtap.cat'), (Join-Path $temp 'ZebraUsbTap.sys') | Out-Null
    $missingRejected = $false
    try { [void](Get-ZebraPackageFiles -PackageInf $inf) } catch { $missingRejected = $true }
    if (-not $missingRejected) { throw 'A SYS outside the package directory was accepted' }
    $packageSys = Join-Path $packageDir 'ZebraUsbTap.sys'
    New-Item -ItemType File -Path $packageSys | Out-Null
    if ((Get-ZebraPackageFiles -PackageInf $inf).Sys -ne $packageSys) { throw 'Incorrect package SYS path returned' }
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
Write-Host 'Driver package identity tests passed.'
