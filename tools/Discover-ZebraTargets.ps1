#requires -version 5.1
$ErrorActionPreference = 'Stop'

Write-Host '=== Windows Zebra printer queues ===' -ForegroundColor Cyan
Get-Printer | Where-Object {
    $_.Name -match 'Zebra|ZDesigner' -or $_.DriverName -match 'Zebra|ZDesigner'
} | Sort-Object Name | Format-Table Name, DriverName, PortName, Shared -AutoSize

Write-Host "`n=== Zebra USB / USBPRINT PnP devices ===" -ForegroundColor Cyan
$devices = Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match '^USB(PRINT)?\\' -and (
        $_.FriendlyName -match 'Zebra|ZDesigner|GK420|ZP 500' -or
        $_.InstanceId -match 'VID_0A5F|ZEBRA'
    )
}

foreach ($d in $devices) {
    Write-Host "`n$($d.FriendlyName)" -ForegroundColor Yellow
    Write-Host "  Class:      $($d.Class)"
    Write-Host "  InstanceId: $($d.InstanceId)"
    $hw = Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_HardwareIds' -ErrorAction SilentlyContinue
    if ($hw.Data) {
        Write-Host '  Hardware IDs:'
        @($hw.Data) | ForEach-Object { Write-Host "    $_" }
    }
    $parent = Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue
    if ($parent.Data) {
        Write-Host "  Parent:     $($parent.Data)"
    }
}
