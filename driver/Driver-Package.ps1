# Shared identity checks for the ZebraUsbTap extension package.
function Get-ZebraPackageFiles {
    param([string]$PackageInf)
    if (-not (Test-Path -LiteralPath $PackageInf)) { throw "Package INF not found: $PackageInf" }
    $directory = Split-Path -Parent $PackageInf
    $sys = Join-Path $directory 'ZebraUsbTap.sys'
    $cat = Join-Path $directory 'zebrausbtap.cat'
    foreach ($path in @($sys,$cat)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing file in the package directory: $path" }
    }
    return [pscustomobject]@{ Inf=$PackageInf; Sys=$sys; Cat=$cat }
}

function Get-ZebraDriverPackages {
    @(Get-WindowsDriver -Online -ErrorAction Stop | Where-Object {
        $_.Driver -match '^oem\d+\.inf$' -and
        [IO.Path]::GetFileName([string]$_.OriginalFileName) -ieq 'ZebraUsbTap.inf' -and
        $_.ProviderName -ieq 'Local Zebra Archive' -and $_.ClassName -ieq 'Extension'
    })
}

function Resolve-ZebraDriverPackage {
    param([object[]]$Packages, [string]$PublishedName)
    $verified = @($Packages | Where-Object {
        $_.Driver -match '^oem\d+\.inf$' -and
        [IO.Path]::GetFileName([string]$_.OriginalFileName) -ieq 'ZebraUsbTap.inf' -and
        $_.ProviderName -ieq 'Local Zebra Archive' -and $_.ClassName -ieq 'Extension'
    })
    if ($PublishedName) {
        if ($PublishedName -notmatch '^oem\d+\.inf$') { throw 'Published name must be an oemNN.inf file name.' }
        $verified = @($verified | Where-Object { $_.Driver -ieq $PublishedName })
    }
    if ($verified.Count -ne 1) {
        throw 'Could not uniquely verify the ZebraUsbTap driver package in the Windows driver store. Inspect Get-WindowsDriver -Online before proceeding.'
    }
    return [string]$verified[0].Driver
}

function Restart-ZebraDevices {
    param([string[]]$HardwareIds)
    foreach ($hardware in $HardwareIds) {
        $matches = @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "$hardware*" })
        foreach ($device in $matches) {
            Write-Host "Restarting $($device.InstanceId)" -ForegroundColor Cyan
            & pnputil.exe /restart-device $device.InstanceId
            if ($LASTEXITCODE -ne 0) { throw "Device restart failed ($LASTEXITCODE): $($device.InstanceId)" }
        }
    }
}
