#requires -version 5.1
$ErrorActionPreference = 'Continue'

$targets = @(
    @{ Name='GK420d'; Hardware='USB\VID_0A5F&PID_0080'; Queue='ZDesigner GK420d'; Port='USB002' },
    @{ Name='ZP500';  Hardware='USB\VID_0A5F&PID_0062'; Queue='Zebra ZP 500 (ZPL)'; Port='USB007' }
)

Write-Host '=== Printer queues ===' -ForegroundColor Cyan
foreach ($t in $targets) {
    $p = Get-Printer -Name $t.Queue -ErrorAction SilentlyContinue
    if ($p) {
        $ok = ($p.PortName -eq $t.Port)
        Write-Host ("{0}: {1} -> {2}" -f $t.Name,$p.Name,$p.PortName) -ForegroundColor $(if($ok){'Green'}else{'Yellow'})
    } else {
        Write-Host "$($t.Name): queue '$($t.Queue)' not found" -ForegroundColor Red
    }
}

Write-Host "`n=== Physical USB filter attachment ===" -ForegroundColor Cyan
foreach ($t in $targets) {
    $matches = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "$($t.Hardware)*" }
    if (-not $matches) {
        Write-Host "$($t.Name): no present physical USB device matching $($t.Hardware)" -ForegroundColor Red
        continue
    }
    foreach ($d in $matches) {
        $filters = Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_CompoundUpperFilters' -ErrorAction SilentlyContinue
        $attached = @($filters.Data) -contains 'ZebraUsbTap'
        Write-Host "$($t.Name): $($d.InstanceId)" -ForegroundColor $(if($attached){'Green'}else{'Yellow'})
        Write-Host "  ZebraUsbTap attached: $attached"
    }
}

Write-Host "`n=== Kernel service ===" -ForegroundColor Cyan
sc.exe query ZebraUsbTap

Write-Host "`n=== Archive server ===" -ForegroundColor Cyan
try {
    $root = Split-Path -Parent $PSScriptRoot
    $config = Get-Content (Join-Path $root 'config.json') -Raw | ConvertFrom-Json
    $token = (Get-Content (Join-Path $root 'data\private\api-token') -Raw).Trim()
    $headers = @{ Authorization = "Bearer $token" }
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:$($config.listen_port)/api/status" -Headers $headers -TimeoutSec 3
    $s | ConvertTo-Json -Depth 6
} catch {
    Write-Host "Archive API not reachable or token inaccessible: $($_.Exception.Message)" -ForegroundColor Yellow
}
